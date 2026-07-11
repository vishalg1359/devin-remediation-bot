---
name: testing-remediation-swarm
description: Test the Devin dependency-upgrade remediation bot (FastAPI scanner + orchestrator + dashboard) end-to-end in mock mode. Use when verifying the /scan event trigger, parallel session fan-out, concurrency cap, PR/metrics observability, or the SCAN_MAX_FINDINGS behavior.
---

# Testing the dependency-remediation swarm

The app is a FastAPI service: a dependency **scan** event (`POST /scan`) fans out to N parallel
mock Devin sessions (one per capped dependency), each resolving to an opened PR, surfaced on a
live dashboard (`/`) + JSON (`/api/metrics`) + Prometheus (`/metrics`).

## Run it (mock mode — zero cost, no API key)
```bash
cd <repo> && . .venv/bin/activate && rm -f data/remediation.db
nohup env SCAN_PYPROJECT_PATH=/path/to/superset/pyproject.toml SCAN_MAX_FINDINGS=5 \
  RECONCILE_INTERVAL_SECONDS=3 MAX_CONCURRENT_SESSIONS=3 \
  uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/uvicorn.log 2>&1 &
```
- Lower `RECONCILE_INTERVAL_SECONDS` (e.g. 3) so sessions progress fast enough to demo/record.
- Point `SCAN_PYPROJECT_PATH` at a real `pyproject.toml` so titles show real deps (e.g.
  `Upgrade \`celery\` past its \`<6\` cap`); leaving it blank uses a curated fallback set.
- Mock mode = header shows `Devin: MOCK`. No real PRs are opened; URLs are `devin-mock-*` / `pull/100x`.

## Golden-path flow
1. Open `http://localhost:8000/` → precondition: all cards 0, "No tasks yet."
2. `curl -X POST localhost:8000/scan` → expect `{"accepted":true,"findings":N,"tasks":[...]}`
   with ≤`MAX_CONCURRENT_SESSIONS` `running` and the rest `queued`.
3. Reload dashboard (auto-refreshes every 10s) → rows progress `running`→`pr_open`→`completed`;
   queued rows get promoted as capacity frees.
4. `GET /api/metrics` → assert `total_tasks`, `prs_opened`, `failed=0`, `success_rate=1.0`,
   `avg_time_to_pr_seconds>0`, `total_acus_consumed>0`. Cross-check against the dashboard cards.

## Regression checks worth running
- **`SCAN_MAX_FINDINGS=0` must dispatch ZERO sessions** (`findings:0, tasks:[]`). This guards the
  `limit=0` falsy bug — 0 means "disable", not "no limit".
- **Queued task keeps its issue body**: with `MAX_CONCURRENT_SESSIONS=1`, a queued task promoted
  later must carry its original body into the prompt (not "(no description provided)").

## Gotchas / lessons
- **Background servers die with one-shot shells.** Start uvicorn via `nohup ... &` inside a
  *persistent* shell (reused `shell_id`), or it gets killed when the exec call returns.
- **Stale server on port 8000.** If a scan returns unexpected results after a restart, a previous
  uvicorn may still hold the port (new one logs `address already in use` and exits, curl hits the
  OLD config). Check `ss -ltnp | grep :8000`, `kill <pid>`, confirm port free, then restart.
- Live-mode auth guards (bearer token on `/scan` + `/simulate/issue`; reject unsigned webhooks)
  only activate when a real `DEVIN_API_KEY` is set — they can't be exercised in mock mode.

## Devin Secrets Needed
- None for mock-mode testing. A real end-to-end run needs `DEVIN_API_KEY` (opens real PRs, spends
  ACUs) and optionally `GITHUB_TOKEN` + `API_TOKEN` (live auth) + `GITHUB_WEBHOOK_SECRET`.
