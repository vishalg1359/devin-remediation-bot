"""SQLite persistence for remediation tasks.

Deliberately tiny and dependency-free (stdlib sqlite3). The task table is the
single source of truth the dashboard and metrics read from.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .models import RemediationTask, TaskStatus, utcnow_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    key            TEXT PRIMARY KEY,
    issue_number   INTEGER NOT NULL,
    issue_title    TEXT NOT NULL,
    repo           TEXT NOT NULL,
    status         TEXT NOT NULL,
    session_id     TEXT,
    session_url    TEXT,
    pr_url         TEXT,
    error          TEXT,
    acus_consumed  REAL NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> RemediationTask:
        return RemediationTask(
            issue_number=row["issue_number"],
            issue_title=row["issue_title"],
            repo=row["repo"],
            status=TaskStatus(row["status"]),
            session_id=row["session_id"],
            session_url=row["session_url"],
            pr_url=row["pr_url"],
            error=row["error"],
            acus_consumed=row["acus_consumed"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get(self, key: str) -> RemediationTask | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE key = ?", (key,)).fetchone()
        return self._row_to_task(row) if row else None

    def upsert(self, task: RemediationTask) -> RemediationTask:
        task.updated_at = utcnow_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (key, issue_number, issue_title, repo, status,
                    session_id, session_url, pr_url, error, acus_consumed,
                    created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                    issue_title=excluded.issue_title,
                    status=excluded.status,
                    session_id=excluded.session_id,
                    session_url=excluded.session_url,
                    pr_url=excluded.pr_url,
                    error=excluded.error,
                    acus_consumed=excluded.acus_consumed,
                    updated_at=excluded.updated_at
                """,
                (
                    task.key(), task.issue_number, task.issue_title, task.repo,
                    task.status.value, task.session_id, task.session_url,
                    task.pr_url, task.error, task.acus_consumed,
                    task.created_at, task.updated_at,
                ),
            )
        return task

    def list_tasks(self) -> list[RemediationTask]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [self._row_to_task(r) for r in rows]

    def active(self) -> list[RemediationTask]:
        """Tasks still tied to a live/queued Devin session."""
        return [
            t for t in self.list_tasks()
            if t.status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.PR_OPEN)
        ]
