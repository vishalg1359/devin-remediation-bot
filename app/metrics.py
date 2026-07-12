"""Derive observability metrics from the task store.

Answers the one question an engineering leader asks: "Is this working?"
"""

from __future__ import annotations

from datetime import datetime

from .config import settings
from .models import RemediationTask, TaskStatus
from .store import Store


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def compute_metrics(store: Store) -> dict:
    tasks = store.list_tasks()
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.status.value] = by_status.get(t.status.value, 0) + 1

    total = len(tasks)
    completed = by_status.get(TaskStatus.COMPLETED.value, 0)
    pr_open = by_status.get(TaskStatus.PR_OPEN.value, 0)
    failed = by_status.get(TaskStatus.FAILED.value, 0)
    active = (
        by_status.get(TaskStatus.RUNNING.value, 0)
        + by_status.get(TaskStatus.QUEUED.value, 0)
        + pr_open
    )
    # Terminal outcomes we can judge success on.
    resolved = completed + pr_open
    finished = resolved + failed
    success_rate = round(resolved / finished, 3) if finished else None

    # Mean time from intake to PR opened, for tasks that produced a PR.
    durations = []
    for t in tasks:
        if t.pr_url:
            durations.append((_parse(t.updated_at) - _parse(t.created_at)).total_seconds())
    avg_time_to_pr = round(sum(durations) / len(durations), 1) if durations else None

    prs_opened = sum(1 for t in tasks if t.pr_url)
    # Business-impact proxy that always tracks real progress: each opened PR is
    # a major upgrade a human would otherwise grind through by hand.
    hours_saved = round(prs_opened * settings.est_hours_saved_per_upgrade, 1)

    return {
        "total_tasks": total,
        "active_tasks": active,
        "prs_opened": prs_opened,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "avg_time_to_pr_seconds": avg_time_to_pr,
        "eng_hours_saved": hours_saved,
        "by_status": by_status,
    }


def prometheus_text(store: Store) -> str:
    """Expose the same numbers in Prometheus text format for scraping."""
    m = compute_metrics(store)
    lines = [
        "# HELP remediation_tasks_total Total remediation tasks seen.",
        "# TYPE remediation_tasks_total counter",
        f"remediation_tasks_total {m['total_tasks']}",
        "# HELP remediation_prs_opened_total PRs opened by Devin.",
        "# TYPE remediation_prs_opened_total counter",
        f"remediation_prs_opened_total {m['prs_opened']}",
        "# HELP remediation_tasks_failed_total Failed remediation tasks.",
        "# TYPE remediation_tasks_failed_total counter",
        f"remediation_tasks_failed_total {m['failed']}",
        "# HELP remediation_tasks_active Currently active tasks.",
        "# TYPE remediation_tasks_active gauge",
        f"remediation_tasks_active {m['active_tasks']}",
        "# HELP remediation_eng_hours_saved Estimated senior-engineer hours saved.",
        "# TYPE remediation_eng_hours_saved gauge",
        f"remediation_eng_hours_saved {m['eng_hours_saved']}",
    ]
    if m["success_rate"] is not None:
        lines += [
            "# HELP remediation_success_rate Resolved / finished tasks.",
            "# TYPE remediation_success_rate gauge",
            f"remediation_success_rate {m['success_rate']}",
        ]
    return "\n".join(lines) + "\n"
