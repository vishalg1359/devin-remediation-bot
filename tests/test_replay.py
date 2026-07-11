"""Tests for the zero-cost replay client."""

from __future__ import annotations

import json

from app.devin_client import ReplayDevinClient


def _fixture(tmp_path):
    path = tmp_path / "real_run.json"
    path.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "dep": "flask-cors",
                        "session_id": "devin-real-abc",
                        "session_url": "https://app.devin.ai/sessions/abc",
                        "pr_url": "https://github.com/acme/superset/pull/101",
                        "acus_consumed": 6.0,
                        "status": "exit",
                    },
                    {
                        "dep": "marshmallow",
                        "session_id": "devin-real-def",
                        "session_url": "https://app.devin.ai/sessions/def",
                        "pr_url": None,
                        "acus_consumed": 4.0,
                        "status": "error",
                    },
                ]
            }
        )
    )
    return str(path)


def test_replay_resolves_to_recorded_pr(tmp_path):
    client = ReplayDevinClient(_fixture(tmp_path))
    info = client.create_session(
        prompt="Issue #1: Upgrade `flask-cors` past its `<7` cap",
        title="Remediate acme/superset#1: Upgrade `flask-cors` past its `<7` cap",
        tags=[],
    )
    assert info.session_id == "devin-real-abc"
    assert info.url == "https://app.devin.ai/sessions/abc"

    # First poll still running, second resolves to the real recorded PR.
    client.get_session(info.session_id)
    final = client.get_session(info.session_id)
    assert final.status == "exit"
    assert final.pr_url == "https://github.com/acme/superset/pull/101"
    assert final.acus_consumed == 6.0


def test_replay_replays_recorded_failure(tmp_path):
    client = ReplayDevinClient(_fixture(tmp_path))
    info = client.create_session(
        prompt="Upgrade `marshmallow`",
        title="Remediate acme/superset#2: Upgrade `marshmallow` past its `<5` cap",
        tags=[],
    )
    client.get_session(info.session_id)
    final = client.get_session(info.session_id)
    assert final.status == "error"
    assert final.pr_url is None
