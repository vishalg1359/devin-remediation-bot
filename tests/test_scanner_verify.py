"""Tests for PyPI-verified scanning (dropping no-op version caps)."""

from __future__ import annotations

import app.scanner as scanner
from app.scanner import Finding, scan, upgrade_available


def _finding(name: str, constraint: str, cap: str) -> Finding:
    return Finding(name=name, current_constraint=constraint, capped_below_major=cap)


def test_upgrade_available_true_when_latest_blocked(monkeypatch):
    monkeypatch.setattr(scanner, "_pypi_latest_version", lambda name: "8.0.1")
    # redis pinned <6 but latest is 8 -> a real upgrade is blocked.
    assert upgrade_available(_finding("redis", "redis>=5.0.0, <6.0", "6")) is True


def test_upgrade_available_false_for_noop_cap(monkeypatch):
    monkeypatch.setattr(scanner, "_pypi_latest_version", lambda name: "6.0.5")
    # flask-cors pinned <7 and latest is 6.0.5 -> cap already allows newest.
    assert upgrade_available(_finding("flask-cors", "flask-cors>=6.0.5, <7.0", "7")) is False


def test_upgrade_available_none_when_unknown(monkeypatch):
    monkeypatch.setattr(scanner, "_pypi_latest_version", lambda name: None)
    assert upgrade_available(_finding("whatever", "whatever<2", "2")) is None


def test_scan_verify_drops_noop_caps(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\n'
        'dependencies = [\n'
        '  "redis>=5.0.0, <6.0",\n'          # latest 8 -> kept
        '  "flask-cors>=6.0.5, <7.0",\n'     # latest 6.0.5 -> dropped
        ']\n'
    )
    latest = {"redis": "8.0.1", "flask-cors": "6.0.5"}
    monkeypatch.setattr(scanner, "_pypi_latest_version", lambda name: latest.get(name))
    names = {f.name for f in scan(pyproject, verify=True)}
    assert names == {"redis"}
    # Without verification both are returned.
    assert {f.name for f in scan(pyproject, verify=False)} == {"redis", "flask-cors"}
