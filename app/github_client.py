"""Minimal GitHub helpers with a mock fallback.

Used for two things:
  1. The polling trigger: list open issues carrying the trigger label.
  2. Feedback: comment the Devin session / PR link back on the issue.

Live mode uses the REST API with a token. Mock mode returns a small canned set
of issues (mirrors the ones you create in the Superset fork) and logs comments.
"""

from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("github")

class GitHubClient:
    def __init__(self) -> None:
        self._live = settings.github_live
        self._headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
        }

    def _ensure_label(self, repo: str, label: str) -> None:
        if httpx.get(f"https://api.github.com/repos/{repo}/labels/{label}",
                     headers=self._headers, timeout=30).status_code == 200:
            return
        httpx.post(f"https://api.github.com/repos/{repo}/labels", headers=self._headers,
                   json={"name": label, "color": "5be584",
                         "description": "Auto-remediated by Devin"}, timeout=30)

    def file_issue(self, repo: str, title: str, body: str, fallback_number: int) -> int:
        """Create (or find existing) an issue for a finding; return its number.

        In mock mode there is no GitHub call — a synthetic number is returned so
        the pipeline still runs end-to-end.
        """
        if not self._live:
            log.info("[mock github] would file issue on %s: %s", repo, title)
            return fallback_number
        # Dedupe: reuse an existing open issue with the same title.
        for issue in self.list_labeled_issues(repo, settings.trigger_label):
            if issue.get("title") == title:
                return issue["number"]
        self._ensure_label(repo, settings.trigger_label)
        resp = httpx.post(f"https://api.github.com/repos/{repo}/issues", headers=self._headers,
                          json={"title": title, "body": body, "labels": [settings.trigger_label]},
                          timeout=30)
        resp.raise_for_status()
        return resp.json()["number"]

    def list_labeled_issues(self, repo: str, label: str) -> list[dict]:
        if not self._live:
            return []
        url = f"https://api.github.com/repos/{repo}/issues"
        resp = httpx.get(url, headers=self._headers,
                         params={"labels": label, "state": "open", "per_page": 50}, timeout=30)
        resp.raise_for_status()
        # Filter out PRs, which the issues endpoint also returns.
        return [i for i in resp.json() if "pull_request" not in i]

    def comment(self, repo: str, issue_number: int, body: str) -> None:
        if not self._live:
            log.info("[mock github] comment on %s#%s: %s", repo, issue_number, body)
            return
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
        resp = httpx.post(url, headers=self._headers, json={"body": body}, timeout=30)
        resp.raise_for_status()
