"""Create the dependency-upgrade issues in your Superset fork (Part 1).

Runs the scanner and files one GitHub issue per finding, labeled so the
automation can pick them up. Requires GITHUB_TOKEN + TARGET_REPO.

Usage:
    GITHUB_TOKEN=ghp_... TARGET_REPO=you/superset python scripts/seed_issues.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402
from app.github_client import GitHubClient  # noqa: E402
from app.scanner import scan  # noqa: E402


def main() -> None:
    if not settings.github_live:
        sys.exit("GITHUB_TOKEN is required to create issues.")
    gh = GitHubClient()
    findings = scan(settings.scan_pyproject_path, limit=settings.scan_max_findings)
    for i, finding in enumerate(findings, start=1):
        number = gh.file_issue(settings.target_repo, finding.title(), finding.body(), fallback_number=i)
        print(f"issue #{number}: {finding.title()}")


if __name__ == "__main__":
    main()
