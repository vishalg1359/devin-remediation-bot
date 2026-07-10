"""Runtime configuration, loaded from environment variables.

Everything here has a safe default so the service boots and demos end-to-end
with zero secrets. Supplying real credentials flips it from "mock" to "live".
"""

from __future__ import annotations

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

    # --- Scanner (the event source) ---
    # Path to the target repo's pyproject.toml to scan for capped dependencies.
    # When unset/missing, the scanner falls back to curated real Superset caps.
    scan_pyproject_path: str | None = None
    # Cap how many findings a single scan dispatches (demo / cost guardrail).
    scan_max_findings: int = 3

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

    @property
    def devin_live(self) -> bool:
        return bool(self.devin_api_key)

    @property
    def github_live(self) -> bool:
        return bool(self.github_token)


settings = Settings()
