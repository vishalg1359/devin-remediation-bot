"""FastAPI entrypoint: event triggers, observability, and lifecycle wiring.

Endpoints:
  POST /webhook/github   -> event-driven trigger (GitHub `issues` events)
  POST /simulate/issue   -> manual trigger for demos/tests (no GitHub needed)
  GET  /                 -> HTML dashboard (observability)
  GET  /api/tasks        -> task list as JSON
  GET  /api/metrics      -> metrics as JSON
  GET  /metrics          -> Prometheus text metrics
  GET  /healthz          -> liveness
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from .github_client import GitHubClient
from .metrics import compute_metrics, prometheus_text
from .orchestrator import Orchestrator
from .store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("app")

store = Store(settings.database_path)
orchestrator = Orchestrator(store)
scheduler = BackgroundScheduler(daemon=True)

_templates = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
)


def _verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


def _authorize_trigger(authorization: str | None, x_api_token: str | None) -> None:
    """Guard cost-incurring trigger endpoints (/scan, /simulate/issue).

    When a real Devin key is configured, calls can spend ACUs, so a bearer
    token is mandatory. In mock mode (no spend) the token is optional, keeping
    local demos frictionless.
    """
    token = settings.api_token
    if not token:
        if settings.devin_live:
            raise HTTPException(
                status_code=503,
                detail="API_TOKEN must be set to expose trigger endpoints while running live",
            )
        return
    provided = None
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):]
    elif x_api_token:
        provided = x_api_token
    if not provided or not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="invalid or missing API token")


def _scheduled_scan() -> None:
    """Scheduled trigger: run a dependency scan and dispatch remediations."""
    try:
        orchestrator.dispatch_scan(settings.target_repo)
    except Exception:  # noqa: BLE001 - trigger must never crash the scheduler
        log.exception("scheduled scan failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(orchestrator.reconcile, "interval",
                      seconds=settings.reconcile_interval_seconds, id="reconcile")
    if settings.enable_polling_trigger:
        scheduler.add_job(_scheduled_scan, "interval",
                          seconds=settings.poll_interval_seconds, id="scheduled_scan")
        log.info("scheduled scan trigger enabled (every %ss)", settings.poll_interval_seconds)
    scheduler.start()
    log.info("started; devin_live=%s github_live=%s", settings.devin_live, settings.github_live)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Devin Auto-Remediation Bot", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "devin_live": settings.devin_live,
        "github_live": settings.github_live,
        "demo_replay": settings.demo_replay,
    }


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
):
    body = await request.body()
    if settings.github_webhook_secret:
        if not _verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="invalid signature")
    elif settings.devin_live:
        # Never accept unsigned webhooks when a real Devin key can spend ACUs.
        raise HTTPException(
            status_code=503,
            detail="GITHUB_WEBHOOK_SECRET must be set to accept webhooks while running live",
        )

    if x_github_event != "issues":
        return {"ignored": True, "reason": f"event {x_github_event} not handled"}

    payload = await request.json()
    action = payload.get("action")
    issue = payload.get("issue", {})
    labels = [l.get("name") for l in issue.get("labels", [])]

    # Trigger when an issue is opened-with-label or labeled with the trigger.
    triggered = (
        (action == "labeled" and payload.get("label", {}).get("name") == settings.trigger_label)
        or (action in {"opened", "reopened"} and settings.trigger_label in labels)
    )
    if not triggered:
        return {"ignored": True, "action": action}

    repo = payload.get("repository", {}).get("full_name", settings.target_repo)
    task = orchestrator.handle_issue(
        {"number": issue["number"], "title": issue["title"], "body": issue.get("body")},
        repo,
    )
    return {"accepted": True, "task": task.key(), "status": task.status.value}


@app.post("/scan")
async def trigger_scan(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
):
    """Event trigger: run a dependency scan and dispatch Devin remediations.

    This is the primary demo trigger — it emulates a scan-results event (a CI
    job, a cron, or a scanner webhook calling in). Optional body: {"repo": ...}.
    """
    _authorize_trigger(authorization, x_api_token)
    repo = None
    try:
        body = await request.json()
        repo = body.get("repo")
    except Exception:  # noqa: BLE001 - empty body is fine
        pass
    tasks = orchestrator.dispatch_scan(repo)
    return {
        "accepted": True,
        "findings": len(tasks),
        "tasks": [{"task": t.key(), "status": t.status.value} for t in tasks],
    }


@app.post("/simulate/issue")
async def simulate_issue(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
):
    """Manual trigger to demo a single issue without wiring GitHub webhooks."""
    _authorize_trigger(authorization, x_api_token)
    payload = await request.json()
    if "number" not in payload or "title" not in payload:
        raise HTTPException(status_code=422, detail="require 'number' and 'title'")
    task = orchestrator.handle_issue(payload, payload.get("repo"))
    return {"accepted": True, "task": task.key(), "status": task.status.value}


@app.get("/api/tasks")
def api_tasks() -> JSONResponse:
    return JSONResponse([t.__dict__ | {"status": t.status.value} for t in store.list_tasks()])


@app.get("/api/metrics")
def api_metrics() -> dict:
    return compute_metrics(store)


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    return prometheus_text(store)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    tmpl = _templates.get_template("dashboard.html")
    return tmpl.render(
        metrics=compute_metrics(store),
        tasks=store.list_tasks(),
        settings=settings,
    )
