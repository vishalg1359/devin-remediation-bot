"""Tests for the orchestration flow using the mock Devin client."""

from __future__ import annotations

import os
import tempfile

import pytest

from app.config import settings
from app.models import TaskStatus
from app.orchestrator import Orchestrator, build_prompt
from app.scanner import scan, scan_pyproject
from app.store import Store


@pytest.fixture()
def store() -> Store:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield Store(path)
    os.unlink(path)


@pytest.fixture(autouse=True)
def _deterministic(monkeypatch):
    # Force mock mode and disable random failures for deterministic assertions.
    monkeypatch.setattr(settings, "devin_api_key", None)
    monkeypatch.setattr(settings, "github_token", None)
    import random
    monkeypatch.setattr(random, "random", lambda: 0.99)


ISSUE = {"number": 42, "title": "Fix unused imports", "body": "Remove F401 warnings."}


def test_build_prompt_includes_issue_details():
    prompt = build_prompt(ISSUE, "acme/superset")
    assert "acme/superset" in prompt
    assert "#42" in prompt
    assert "Fix unused imports" in prompt
    assert "Fixes #42" in prompt


def test_handle_issue_starts_session(store):
    orch = Orchestrator(store)
    task = orch.handle_issue(ISSUE, "acme/superset")
    assert task.status == TaskStatus.RUNNING
    assert task.session_id is not None
    assert store.get(task.key()) is not None


def test_handle_issue_is_idempotent(store):
    orch = Orchestrator(store)
    first = orch.handle_issue(ISSUE, "acme/superset")
    second = orch.handle_issue(ISSUE, "acme/superset")
    assert first.session_id == second.session_id
    assert len(store.list_tasks()) == 1


def test_concurrency_cap_queues_extra_work(store, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_sessions", 1)
    orch = Orchestrator(store)
    orch.handle_issue({"number": 1, "title": "one", "body": ""}, "acme/superset")
    queued = orch.handle_issue({"number": 2, "title": "two", "body": ""}, "acme/superset")
    assert queued.status == TaskStatus.QUEUED
    assert queued.session_id is None


def test_reconcile_progresses_to_pr_and_completed(store):
    orch = Orchestrator(store)
    task = orch.handle_issue(ISSUE, "acme/superset")
    key = task.key()
    # Mock client opens a PR after two polls, then exits.
    for _ in range(4):
        orch.reconcile()
    final = store.get(key)
    assert final.pr_url is not None
    assert final.status == TaskStatus.COMPLETED


def test_reconcile_promotes_queued_when_capacity_frees(store, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_sessions", 1)
    orch = Orchestrator(store)
    orch.handle_issue({"number": 1, "title": "one", "body": ""}, "acme/superset")
    orch.handle_issue({"number": 2, "title": "two", "body": ""}, "acme/superset")
    for _ in range(6):
        orch.reconcile()
    statuses = {t.issue_number: t.status for t in store.list_tasks()}
    assert statuses[2] != TaskStatus.QUEUED  # eventually started


def test_scanner_fallback_returns_curated_findings():
    findings = scan(None, limit=3)
    assert len(findings) == 3
    assert all(f.capped_below_major for f in findings)
    assert "Upgrade" in findings[0].title()


def test_scanner_parses_pyproject(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\n'
        'dependencies = [\n'
        '  "marshmallow>=3.0, <5",\n'
        '  "packaging",\n'          # no upper bound -> ignored
        '  "flask>=2.2.5, <4.0.0",\n'
        ']\n'
    )
    findings = scan_pyproject(pyproject)
    names = {f.name for f in findings}
    assert names == {"marshmallow", "flask"}  # only capped deps flagged


def test_scan_limit_zero_returns_no_findings():
    # limit=0 means "dispatch nothing", not "no limit".
    assert scan(None, limit=0) == []


def test_promoted_task_retains_issue_body(store, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_sessions", 1)
    orch = Orchestrator(store)

    prompts: list[str] = []
    real_create = orch.devin.create_session

    def _record(prompt, **kwargs):
        prompts.append(prompt)
        return real_create(prompt=prompt, **kwargs)

    monkeypatch.setattr(orch.devin, "create_session", _record)

    orch.handle_issue({"number": 1, "title": "one", "body": "first body"}, "acme/superset")
    orch.handle_issue({"number": 2, "title": "two", "body": "second body"}, "acme/superset")
    assert store.get("acme/superset#2").issue_body == "second body"

    for _ in range(6):
        orch.reconcile()

    # The promoted session must carry the original body, not "(no description provided)".
    assert any("second body" in p for p in prompts)
    assert all("(no description provided)" not in p for p in prompts)


def test_dispatch_scan_files_issues_and_starts_sessions(store, monkeypatch):
    monkeypatch.setattr(settings, "scan_pyproject_path", None)
    monkeypatch.setattr(settings, "scan_max_findings", 3)
    monkeypatch.setattr(settings, "max_concurrent_sessions", 5)
    orch = Orchestrator(store)
    tasks = orch.dispatch_scan("acme/superset")
    assert len(tasks) == 3
    assert all(t.session_id for t in tasks)  # each finding started a session
