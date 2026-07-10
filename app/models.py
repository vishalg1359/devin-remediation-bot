"""Domain models shared across the service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    """Lifecycle of a single remediation task."""

    QUEUED = "queued"          # accepted, not yet handed to Devin
    RUNNING = "running"        # Devin session working
    PR_OPEN = "pr_open"        # Devin opened a pull request
    COMPLETED = "completed"    # session finished (PR opened or work done)
    FAILED = "failed"          # session errored or we failed to start it
    SKIPPED = "skipped"        # duplicate / not eligible


# Devin session statuses that mean "no longer actively working".
DEVIN_TERMINAL = {"exit", "blocked", "finished", "expired", "error"}


@dataclass
class RemediationTask:
    """One issue -> one Devin session -> (hopefully) one PR."""

    issue_number: int
    issue_title: str
    repo: str
    status: TaskStatus = TaskStatus.QUEUED
    session_id: str | None = None
    session_url: str | None = None
    pr_url: str | None = None
    error: str | None = None
    acus_consumed: float = 0.0
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def key(self) -> str:
        return f"{self.repo}#{self.issue_number}"
