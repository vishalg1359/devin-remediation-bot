#!/usr/bin/env bash
# One command to run the control tower in any mode.
#
#   ./scripts/run.sh replay      # recorded real run, zero API calls (default)
#   ./scripts/run.sh live        # real Devin sessions (needs DEVIN_API_KEY + API_TOKEN)
#   ./scripts/run.sh mock        # simulated sessions, no spend
#
# Optional 2nd arg is the port (default 8000):  ./scripts/run.sh replay 8001
set -euo pipefail

MODE="${1:-replay}"
PORT="${2:-8000}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export MODE
export DATABASE_PATH="${DATABASE_PATH:-data/${MODE}.db}"
export TARGET_REPO="${TARGET_REPO:-vishalg1359/superset}"
export MAX_CONCURRENT_SESSIONS="${MAX_CONCURRENT_SESSIONS:-3}"
export RECONCILE_INTERVAL_SECONDS="${RECONCILE_INTERVAL_SECONDS:-5}"

case "$MODE" in
  replay)
    echo "▶ REPLAY — replays a recorded real run to the real PRs, zero API calls"
    rm -f "$DATABASE_PATH"   # start from an empty board every take
    ;;
  mock)
    echo "▶ MOCK — simulated sessions, no spend"
    rm -f "$DATABASE_PATH"
    ;;
  live)
    echo "▶ LIVE — real Devin sessions (needs DEVIN_API_KEY + API_TOKEN)"
    if [ -z "${DEVIN_API_KEY:-}" ]; then
      echo "  ⚠  DEVIN_API_KEY is not set — falling back to MOCK." >&2
    fi
    ;;
  *)
    echo "unknown mode '$MODE' — use: mock | replay | live" >&2
    exit 1
    ;;
esac

echo "  → http://localhost:${PORT}/  (db: ${DATABASE_PATH})"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
