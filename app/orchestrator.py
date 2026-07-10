"""Core orchestration: GitHub issue -> Devin session -> pull request.

The orchestrator is intentionally the only place that mutates task state. It:
  * turns an issue into a well-structured Devin prompt,
  * enforces a concurrency cap (cost / blast-radius guardrail),
  * deduplicates so the same issue is never worked twice,
  * reconciles live session status and records PR links + ACUs,
  * comments progress back on the issue.
"""

from __future__ import annotations

import logging

from .config import settings
from .devin_client import DevinClientError, SessionInfo, build_devin_client
from .github_client import GitHubClient
from .models import DEVIN_TERMINAL, RemediationTask, TaskStatus, utcnow_iso
from .scanner import Finding, scan
from .store import Store

log = logging.getLogger("orchestrator")

PROMPT_TEMPLATE = """\
You are remediating a dependency-upgrade issue in the repository {repo}.

Issue #{number}: {title}

{body}

This is the work a version-bump bot (e.g. Dependabot) cannot do: not just
changing the pinned version, but fixing the code that breaks under the new
major and getting the test suite green.

Instructions:
1. Work on a new branch off the default branch.
2. Raise the dependency's version cap in pyproject.toml to allow the new major.
3. Update all code that breaks under the new version (APIs renamed/removed,
   changed signatures, deprecations).
4. Run the repository's relevant tests/linters until they pass.
5. Open a pull request that references this issue (e.g. "Fixes #{number}"),
   summarizing what broke and how you fixed it.

Do not make unrelated changes. If the upgrade is too large to complete safely,
open a draft PR with the progress made and clearly list what remains.
"""


def build_prompt(issue: dict, repo: str) -> str:
    return PROMPT_TEMPLATE.format(
        repo=repo,
        number=issue["number"],
        title=issue["title"],
        body=(issue.get("body") or "").strip() or "(no description provided)",
    )


class Orchestrator:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.devin = build_devin_client()
        self.github = GitHubClient()

    # --- event: scan results ---------------------------------------------
    def dispatch_scan(self, repo: str | None = None) -> list[RemediationTask]:
        """The event trigger: scan for outdated deps, file issues, start Devin.

        Each finding becomes a GitHub issue (Part 1 deliverable) which is then
        handed to Devin (Part 2). Returns the tasks created/updated.
        """
        repo = repo or settings.target_repo
        findings: list[Finding] = scan(settings.scan_pyproject_path, limit=settings.scan_max_findings)
        log.info("scan produced %d finding(s) for %s", len(findings), repo)
        tasks: list[RemediationTask] = []
        for i, finding in enumerate(findings, start=1):
            issue_number = self.github.file_issue(repo, finding.title(), finding.body(), fallback_number=i)
            issue = {"number": issue_number, "title": finding.title(), "body": finding.body()}
            tasks.append(self.handle_issue(issue, repo))
        return tasks

    # --- intake -----------------------------------------------------------
    def handle_issue(self, issue: dict, repo: str | None = None) -> RemediationTask:
        """Idempotently accept an issue and, if capacity allows, start Devin."""
        repo = repo or settings.target_repo
        task = RemediationTask(
            issue_number=issue["number"],
            issue_title=issue["title"],
            repo=repo,
        )
        existing = self.store.get(task.key())
        if existing and existing.status != TaskStatus.FAILED:
            log.info("issue %s already tracked (%s); skipping", task.key(), existing.status)
            return existing

        if len(self.store.active()) >= settings.max_concurrent_sessions:
            log.info("at concurrency cap; queuing %s", task.key())
            task.status = TaskStatus.QUEUED
            return self.store.upsert(task)

        return self._start_session(task, issue, repo)

    def _start_session(self, task: RemediationTask, issue: dict, repo: str) -> RemediationTask:
        prompt = build_prompt(issue, repo)
        try:
            info: SessionInfo = self.devin.create_session(
                prompt=prompt,
                title=f"Remediate {repo}#{issue['number']}: {issue['title'][:60]}",
                tags=["auto-remediation", f"repo:{repo}"],
            )
        except DevinClientError as exc:
            log.error("failed to start session for %s: %s", task.key(), exc)
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            return self.store.upsert(task)

        task.session_id = info.session_id
        task.session_url = info.url
        task.status = TaskStatus.RUNNING
        self.store.upsert(task)
        self.github.comment(
            repo, issue["number"],
            f"🤖 Devin picked up this issue. Tracking session: {info.url}",
        )
        log.info("started %s -> %s", task.key(), info.session_id)
        return task

    # --- reconciliation ---------------------------------------------------
    def reconcile(self) -> None:
        """Poll live sessions, advance state, then promote queued work."""
        for task in self.store.active():
            if task.status == TaskStatus.QUEUED or not task.session_id:
                continue
            try:
                info = self.devin.get_session(task.session_id)
            except DevinClientError as exc:
                log.warning("reconcile failed for %s: %s", task.key(), exc)
                continue
            self._apply_status(task, info)
        self._promote_queued()

    def _apply_status(self, task: RemediationTask, info: SessionInfo) -> None:
        changed = False
        if info.acus_consumed and info.acus_consumed != task.acus_consumed:
            task.acus_consumed = info.acus_consumed
            changed = True

        if info.pr_url and not task.pr_url:
            task.pr_url = info.pr_url
            task.status = TaskStatus.PR_OPEN
            changed = True
            self.github.comment(
                task.repo, task.issue_number,
                f"✅ Devin opened a pull request: {info.pr_url}",
            )

        if info.status in DEVIN_TERMINAL:
            if info.status == "error":
                task.status = TaskStatus.FAILED
                task.error = "Devin session errored"
                self.github.comment(
                    task.repo, task.issue_number,
                    "⚠️ Devin's session errored before opening a PR. Needs a human look.",
                )
            else:
                task.status = TaskStatus.COMPLETED if task.pr_url else TaskStatus.FAILED
                if not task.pr_url:
                    task.error = "session finished without a PR"
            changed = True

        if changed:
            task.updated_at = utcnow_iso()
            self.store.upsert(task)

    def _promote_queued(self) -> None:
        capacity = settings.max_concurrent_sessions - len(
            [t for t in self.store.active() if t.session_id]
        )
        if capacity <= 0:
            return
        queued = [t for t in self.store.list_tasks() if t.status == TaskStatus.QUEUED]
        for task in queued[:capacity]:
            issue = {"number": task.issue_number, "title": task.issue_title, "body": ""}
            self._start_session(task, issue, task.repo)
