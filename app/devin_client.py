"""Thin client for the Devin API with a built-in mock mode.

Live mode talks to the real Devin REST API (https://api.devin.ai/v1).
Mock mode simulates the session lifecycle in-memory so the whole system can be
demoed and tested without an API key or spending ACUs. The rest of the code
does not know or care which mode is active — it just calls create/get.
"""

from __future__ import annotations

import itertools
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import settings


@dataclass
class SessionInfo:
    session_id: str
    status: str                 # new | running | blocked | exit | error ...
    url: str | None = None
    pr_url: str | None = None
    acus_consumed: float = 0.0


class DevinClientError(RuntimeError):
    pass


class LiveDevinClient:
    """Talks to the real Devin API."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def create_session(self, prompt: str, title: str, tags: list[str]) -> SessionInfo:
        body: dict = {"prompt": prompt, "title": title, "tags": tags, "idempotent": True}
        if settings.max_acu_per_session:
            body["max_acu_limit"] = settings.max_acu_per_session
        try:
            resp = httpx.post(f"{self._base}/sessions", json=body, headers=self._headers, timeout=60)
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - network path
            raise DevinClientError(f"create_session failed: {exc}") from exc
        data = resp.json()
        return SessionInfo(
            session_id=data["session_id"],
            status=data.get("status", "new"),
            url=data.get("url"),
        )

    def get_session(self, session_id: str) -> SessionInfo:
        try:
            resp = httpx.get(f"{self._base}/session/{session_id}", headers=self._headers, timeout=30)
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - network path
            raise DevinClientError(f"get_session failed: {exc}") from exc
        data = resp.json()
        prs = data.get("pull_requests") or []
        pr_url = None
        if prs:
            pr_url = prs[0].get("pr_url") or prs[0].get("url")
        elif isinstance(data.get("pull_request"), dict):
            pr_url = data["pull_request"].get("url")
        return SessionInfo(
            session_id=session_id,
            status=data.get("status_enum") or data.get("status", "running"),
            url=data.get("url"),
            pr_url=pr_url,
            acus_consumed=float(data.get("acus_consumed", 0) or 0),
        )


class MockDevinClient:
    """In-memory simulation of the Devin session lifecycle.

    Each created session progresses new -> running -> exit over a few polls,
    then "opens a PR". One in ~8 sessions fails, so failure observability is
    demonstrable too. Timing is compressed for a snappy demo.
    """

    _counter = itertools.count(1)

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    def create_session(self, prompt: str, title: str, tags: list[str]) -> SessionInfo:
        sid = f"devin-mock-{next(self._counter):04d}"
        will_fail = random.random() < 0.12
        self._sessions[sid] = {
            "created": time.time(),
            "polls": 0,
            "will_fail": will_fail,
            "title": title,
        }
        return SessionInfo(sid, status="running", url=f"https://app.devin.ai/sessions/{sid}")

    def get_session(self, session_id: str) -> SessionInfo:
        s = self._sessions.get(session_id)
        if s is None:
            raise DevinClientError(f"unknown session {session_id}")
        s["polls"] += 1
        url = f"https://app.devin.ai/sessions/{session_id}"
        # Progress after a couple of reconcile cycles.
        if s["polls"] < 2:
            return SessionInfo(session_id, "running", url=url, acus_consumed=round(s["polls"] * 1.5, 1))
        if s["will_fail"]:
            return SessionInfo(session_id, "error", url=url, acus_consumed=3.0)
        pr_num = 1000 + int(session_id.split("-")[-1])
        pr_url = f"https://github.com/{settings.target_repo}/pull/{pr_num}"
        return SessionInfo(session_id, "exit", url=url, pr_url=pr_url, acus_consumed=round(2 + s["polls"], 1))


class ReplayDevinClient:
    """Replays a previously-recorded real run from a fixture file.

    Zero API calls, zero ACUs. Each created session is matched to a recorded
    outcome by dependency name, then the lifecycle animates running -> exit over
    a couple of polls, resolving to the *real* PR/session links and ACU numbers
    captured during the live run. This lets the dashboard be demoed repeatedly
    for free while still showing genuine Devin PRs.
    """

    _DEP_RE = re.compile(r"`([A-Za-z0-9_.\-]+)`")
    _counter = itertools.count(1)

    def __init__(self, fixture_path: str) -> None:
        data = json.loads(Path(fixture_path).read_text())
        self._outcomes: dict[str, dict] = {r["dep"]: r for r in data.get("runs", [])}
        self._sessions: dict[str, dict] = {}

    def _dep(self, *texts: str | None) -> str | None:
        for t in texts:
            m = self._DEP_RE.search(t or "")
            if m:
                return m.group(1)
        return None

    def create_session(self, prompt: str, title: str, tags: list[str]) -> SessionInfo:
        dep = self._dep(title, prompt)
        outcome = self._outcomes.get(dep or "", {})
        sid = outcome.get("session_id") or f"devin-replay-{next(self._counter):04d}"
        self._sessions[sid] = {"polls": 0, "outcome": outcome}
        url = outcome.get("session_url") or f"https://app.devin.ai/sessions/{sid}"
        return SessionInfo(sid, status="running", url=url)

    def get_session(self, session_id: str) -> SessionInfo:
        s = self._sessions.get(session_id)
        if s is None:
            raise DevinClientError(f"unknown session {session_id}")
        s["polls"] += 1
        o = s["outcome"]
        url = o.get("session_url") or f"https://app.devin.ai/sessions/{session_id}"
        acus = float(o.get("acus_consumed", 0) or 0)
        if s["polls"] < 2:
            return SessionInfo(session_id, "running", url=url, acus_consumed=round(acus * 0.5, 1))
        pr_url = o.get("pr_url")
        if o.get("status") == "error" or not pr_url:
            return SessionInfo(session_id, "error", url=url, acus_consumed=acus)
        return SessionInfo(session_id, "exit", url=url, pr_url=pr_url, acus_consumed=acus)


DevinClient = LiveDevinClient | MockDevinClient | ReplayDevinClient


def build_devin_client() -> DevinClient:
    if settings.demo_replay:
        return ReplayDevinClient(settings.demo_replay_fixture)  # type: ignore[arg-type]
    if settings.devin_live:
        return LiveDevinClient(settings.devin_api_key, settings.devin_api_base_url)  # type: ignore[arg-type]
    return MockDevinClient()
