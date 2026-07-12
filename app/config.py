"""Runtime configuration, loaded from environment variables.

Everything here has a safe default so the service boots and demos end-to-end
with zero secrets. Supplying real credentials flips it from "mock" to "live".
"""

from __future__ import annotations

from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Devin API ---
    devin_api_key: str | None = None
    devin_api_base_url: str = "https://api.devin.ai/v1"

    # --- GitHub ---
    # Personal access token used to comment on issues / read repo state.
    github_token: str | None = None
    # Fork we operate on, e.g. "vishalg1359/superset".
    target_repo: str = "vishalg1359/superset"
    # Secret shared with GitHub to verify webhook signatures.
    github_webhook_secret: str | None = None
    # Only issues carrying this label are picked up.
    trigger_label: str = "devin-fix"

    # --- API auth ---
    # Bearer token required to call the cost-incurring trigger endpoints
    # (/scan, /simulate/issue). When running live (a real Devin key is set)
    # a token is mandatory; in mock mode it is optional so local demos are
    # frictionless.
    api_token: str | None = None

    # --- Scanner (the event source) ---
    # Path to the target repo's pyproject.toml to scan for capped dependencies.
    # When unset/missing, the scanner falls back to curated real Superset caps.
    scan_pyproject_path: str | None = None
    # Cap how many findings a single scan dispatches (demo / cost guardrail).
    scan_max_findings: int = 3
    # When true, verify each finding against PyPI and drop no-op caps (an upper
    # bound that already allows the latest release), so only genuinely-available
    # upgrades are dispatched. Off by default to keep offline demos deterministic.
    scan_verify_available: bool = False

    # --- Mode ---
    # One switch to pick how sessions are executed:
    #   "mock"   -> simulated sessions, no spend (great for local dev)
    #   "replay" -> re-animate a recorded real run to the real PRs, zero cost
    #   "live"   -> call the real Devin API and open real PRs (needs a key)
    #   "auto"   -> infer: replay if a fixture is present, else live if a key
    #               is set, else mock (preserves the original behaviour).
    mode: str = "auto"

    # Estimated senior-engineer hours a manual major upgrade would take. Used
    # only to surface an at-a-glance business-impact KPI ("hours saved").
    est_hours_saved_per_upgrade: float = 6.0

    # --- Orchestration ---
    # Cap concurrent Devin sessions so an automation can't stampede.
    max_concurrent_sessions: int = 3
    # Optional ACU ceiling per session (cost guardrail); None = Devin default.
    max_acu_per_session: int | None = 10
    # How often (seconds) the reconciler polls Devin for session status.
    reconcile_interval_seconds: int = 20
    # How often (seconds) the polling trigger scans GitHub for new issues.
    poll_interval_seconds: int = 60
    # Enable the scheduled GitHub polling trigger (webhook is primary).
    enable_polling_trigger: bool = False

    # --- Storage ---
    database_path: str = "data/remediation.db"

    # --- Demo replay ---
    # When set to a fixture file, the system replays a previously-recorded real
    # run instead of calling the Devin API: zero cost, fully repeatable, and it
    # resolves to the real PR/session links captured during the live run. Takes
    # precedence over live mode so a saved run can be demoed for free.
    demo_replay_fixture: str | None = None

    # Default fixture used when replay mode is selected without an explicit path.
    _DEFAULT_REPLAY_FIXTURE: ClassVar[str] = "demo/real_run.json"

    @property
    def replay_fixture_path(self) -> str:
        return self.demo_replay_fixture or self._DEFAULT_REPLAY_FIXTURE

    @property
    def resolved_mode(self) -> str:
        """Collapse the `mode` switch (+ legacy env) into one of mock/replay/live."""
        from pathlib import Path

        m = (self.mode or "auto").lower()
        if m == "replay":
            return "replay"
        if m == "live":
            return "live" if self.devin_api_key else "mock"
        if m == "mock":
            return "mock"
        # auto: preserve the original precedence (fixture > key > mock).
        if self.demo_replay_fixture and Path(self.demo_replay_fixture).exists():
            return "replay"
        if self.devin_api_key:
            return "live"
        return "mock"

    @property
    def demo_replay(self) -> bool:
        return self.resolved_mode == "replay"

    @property
    def devin_live(self) -> bool:
        return self.resolved_mode == "live"

    @property
    def github_live(self) -> bool:
        return bool(self.github_token)


settings = Settings()
