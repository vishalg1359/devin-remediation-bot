"""Record a completed real run into a replay fixture.

Reads the task store from a live run and writes a fixture file that
``ReplayDevinClient`` can replay for free — resolving to the *real* PR and
session links captured during the live run. This is what turns one paid run
into an infinitely repeatable, zero-cost demo.

Usage:
    DATABASE_PATH=data/live_run.db python -m scripts.record_run demo/real_run.json
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone

from app.config import settings
from app.store import Store

_DEP_RE = re.compile(r"`([A-Za-z0-9_.\-]+)`")


def _dep(title: str) -> str | None:
    m = _DEP_RE.search(title or "")
    return m.group(1) if m else None


def main() -> None:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "demo/real_run.json"
    store = Store(settings.database_path)
    runs = []
    for t in store.list_tasks():
        dep = _dep(t.issue_title)
        if not dep:
            continue
        runs.append(
            {
                "dep": dep,
                "issue_title": t.issue_title,
                "session_id": t.session_id,
                "session_url": t.session_url,
                "pr_url": t.pr_url,
                "acus_consumed": round(t.acus_consumed, 2),
                "status": "error" if t.status.value == "failed" else "exit",
            }
        )
    fixture = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_db": settings.database_path,
        "runs": runs,
    }
    import os

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(fixture, fh, indent=2)
    print(f"wrote {len(runs)} recorded outcome(s) to {out_path}")
    for r in runs:
        print(f"  {r['dep']:16} {r['status']:6} pr={r['pr_url']}")


if __name__ == "__main__":
    main()
