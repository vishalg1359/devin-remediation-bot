"""Thin client for the Devin API with a built-in mock mode.

Live mode talks to the real Devin REST API (https://api.devin.ai/v1).
Mock mode simulates the session lifecycle in-memory so the whole system can be
demoed and tested without an API key or spending ACUs. The rest of the code
does not know or care which mode is active — it just calls create/get.
"""

from __future__ import annotations

import itertools
import random
import time
from dataclasses import dataclass

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


DevinClient = LiveDevinClient | MockDevinClient


def build_devin_client() -> DevinClient:
    if settings.devin_live:
        return LiveDevinClient(settings.devin_api_key, settings.devin_api_base_url)  # type: ignore[arg-type]
    return MockDevinClient()
