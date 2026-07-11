"""Dependency scanner — the *event source* for the automation.

Parses a target repo's ``pyproject.toml`` and flags dependencies that are
pinned *below* a newer major release (an upper bound like ``<2``). Those caps
are exactly the upgrades that are held back because bumping them breaks code —
the work Dependabot can't finish and Devin can.

Each flagged dependency becomes a "finding". Findings are what trigger Devin
sessions, so a scan run is the event that drives the whole pipeline.

Offline/mock mode returns a curated set of real Superset caps so the system
demos without network access or the repo checked out.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10 fallback
    import tomli as tomllib

log = logging.getLogger("scanner")


@dataclass
class Finding:
    name: str                     # package name, e.g. "marshmallow"
    current_constraint: str       # the raw spec, e.g. ">=3.0, <5"
    capped_below_major: str       # the upper-bound major it's held under, e.g. "5"
    rationale: str = ""
    # A synthetic issue number is assigned when the finding is filed as an issue.
    issue_number: int | None = None

    def title(self) -> str:
        return f"Upgrade `{self.name}` past its `<{self.capped_below_major}` cap"

    def body(self) -> str:
        return (
            f"The dependency `{self.name}` is pinned `{self.current_constraint}` in "
            f"`pyproject.toml`, capping it below major version {self.capped_below_major}.\n\n"
            f"{self.rationale}\n\n"
            "Task: raise the upper bound to allow the next major, update any code that "
            "breaks under the new version, and get the test suite green. Open a PR that "
            "references this issue."
        ).strip()


# Curated real Superset caps (from apache/superset pyproject.toml) for offline
# demo mode. These are genuine upper-bound pins, not invented.
_MOCK_FINDINGS = [
    Finding("marshmallow", ">=3.0, <5", "5",
            "Marshmallow 4 changes schema/validation behavior across many schemas."),
    Finding("flask", ">=2.2.5, <4.0.0", "4",
            "Flask 4 changes app/config setup touched across the app factory."),
    Finding("redis", ">=5.0.0, <6.0", "6",
            "redis-py 6 adjusts client APIs used by the caching/celery layers."),
    Finding("celery", ">=5.6.3, <6.0.0", "6",
            "Celery 6 changes task/config conventions used by async workers."),
    Finding("Pillow", ">=11.0.0, <13", "13",
            "Pillow 13 removes deprecated image APIs used in thumbnail generation."),
]

# Matches a spec's package name and any "<N" upper bound major.
_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")
_UPPER_RE = re.compile(r"<\s*(\d+)")
# Strips a leading package name (and optional extras) from a requirement spec,
# leaving just the version specifier, e.g. "pandas[excel]>=2,<3" -> ">=2,<3".
_NAME_PREFIX_RE = re.compile(r"^\s*[A-Za-z0-9_.\-]+(\[[^\]]*\])?")


def _pypi_latest_version(name: str) -> str | None:
    try:
        url = f"https://pypi.org/pypi/{name}/json"
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - fixed https host
            return json.load(resp)["info"]["version"]
    except Exception as exc:  # noqa: BLE001 - network/parse errors are non-fatal
        log.warning("pypi lookup failed for %s: %s", name, exc)
        return None


def upgrade_available(finding: Finding) -> bool | None:
    """Is the latest PyPI release actually blocked by the current constraint?

    Returns True when a genuinely newer release exists that the pin forbids
    (a real upgrade), False when the pin already allows the latest release
    (a no-op cap, e.g. `<7` when the newest version is 6.x), and None when it
    cannot be determined (offline / parse error) so callers can keep the
    finding rather than silently drop it.
    """
    latest = _pypi_latest_version(finding.name)
    if latest is None:
        return None
    try:
        from packaging.specifiers import SpecifierSet

        spec_text = _NAME_PREFIX_RE.sub("", finding.current_constraint).strip()
        if not spec_text:
            return None
        # If the newest published release does not satisfy the pin, the pin is
        # holding back a real upgrade.
        return not SpecifierSet(spec_text).contains(latest, prereleases=False)
    except Exception as exc:  # noqa: BLE001 - be conservative, keep the finding
        log.warning("could not evaluate constraint for %s: %s", finding.name, exc)
        return None


def scan_pyproject(pyproject_path: str | Path) -> list[Finding]:
    """Return findings for dependencies capped below a newer major."""
    path = Path(pyproject_path)
    data = tomllib.loads(path.read_text())
    deps = data.get("project", {}).get("dependencies", [])
    findings: list[Finding] = []
    for spec in deps:
        name_match = _SPEC_RE.match(spec)
        upper_match = _UPPER_RE.search(spec)
        if not name_match or not upper_match:
            continue
        findings.append(
            Finding(
                name=name_match.group(1),
                current_constraint=spec.strip(),
                capped_below_major=upper_match.group(1),
                rationale="Pinned below a newer major; upgrading likely requires code changes.",
            )
        )
    return findings


def scan(
    pyproject_path: str | Path | None = None,
    limit: int | None = None,
    verify: bool = False,
) -> list[Finding]:
    """Run a scan. Falls back to curated real findings when no repo is given.

    When ``verify`` is set, each finding is checked against PyPI and no-op caps
    (an upper bound that already allows the newest release, e.g. ``<7`` when the
    latest version is 6.x) are dropped, so the scan only surfaces dependencies
    with a genuinely-available newer release to upgrade to. Findings that can't
    be verified (offline / parse error) are kept.
    """
    if pyproject_path and Path(pyproject_path).exists():
        findings = scan_pyproject(pyproject_path)
    else:
        findings = list(_MOCK_FINDINGS)
    if verify:
        findings = [f for f in findings if upgrade_available(f) is not False]
    return findings[:limit] if limit is not None else findings
