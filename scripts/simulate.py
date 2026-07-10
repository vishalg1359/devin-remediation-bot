"""Fire a simulated scan-results event at a running bot — for demos/testing.

Usage:
    python scripts/simulate.py                      # trigger one scan
    python scripts/simulate.py --url http://host:8000

This hits the /scan trigger, which runs a dependency scan and dispatches a
Devin session per finding — so you can demo the full pipeline (scan -> issues
-> Devin sessions -> PRs -> dashboard) with no GitHub wiring or API key.
"""

from __future__ import annotations

import argparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    resp = httpx.post(f"{args.url}/scan", json={}, timeout=60)
    print(f"-> /scan: {resp.status_code}")
    print(resp.json())
    print(f"\nWatch it work: {args.url}/  ·  metrics: {args.url}/api/metrics")


if __name__ == "__main__":
    main()
