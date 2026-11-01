"""
Kiro-Sentry — Autonomous Cloud & Log Sentinel.

FastAPI backend exposing:
  POST /api/v1/telemetry/log   - webhook log ingestion (202 Accepted)
  POST /api/v1/simulate        - anomaly injector (dashboard button)
  GET  /api/v1/meta            - active steering rules + hooks
  GET  /health                 - container healthcheck
  GET  /                       - real-time dashboard
  WS   /ws/telemetry           - real-time event stream
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("kiro-sentry")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Pydantic v2 models
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class LogEvent(BaseModel):
    service: str = Field(..., min_length=1, max_length=120)
    severity: Severity = Severity.ERROR
    message: str = Field(..., min_length=1, max_length=4000)
    stack_trace: str | None = Field(default=None, max_length=20000)
    meta: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None

    @field_validator("service")
    @classmethod
    def _service_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("service must not be blank")
        return v


class RootCause(BaseModel):
    category: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str
    steering_rules: list[str]


class FixSpec(BaseModel):
    id: str
    title: str
    root_cause: RootCause
    steps: list[str]
    patch_preview: str
    estimated_impact: str


class LogAccepted(BaseModel):
    accepted: bool
    event_id: str
    received_at: datetime


# ---------------------------------------------------------------------------
# Steering rules & hooks surfaced to the dashboard
# ---------------------------------------------------------------------------

ACTIVE_STEERING_RULES: list[dict[str, str]] = [
    {"id": "resilience.md", "title": "Resilience — timeout & circuit budgets"},
    {"id": "observability.md", "title": "Observability — structured logs, SLO tags"},
    {"id": "contracts.md", "title": "Contracts — strict Pydantic, no untyped dicts"},
    {"id": "security.md", "title": "Security — least-privilege, no secrets in logs"},
    {"id": "triage.md", "title": "Triage — every incident gets owner + fix spec"},
]

ACTIVE_HOOKS: list[dict[str, str]] = [
    {"id": "lint", "title": "flake8 on save"},
    {"id": "test-on-push", "title": "pytest before commit"},
    {"id": "spec-sync", "title": "requirements.md ↔ code drift check"},
]


# ---------------------------------------------------------------------------
# Root-cause classifier
# ---------------------------------------------------------------------------

_SIGNATURE_RULES: list[tuple[re.Pattern[str], str, float, str, list[str]]] = [
    (
        re.compile(r"TimeoutError|ETIMEDOUT|DeadlineExceeded|timed out", re.I),
        "dependency_timeout",
        0.86,
        "Upstream dependency exceeded its latency budget.",
        ["resilience.md#timeout-budget", "observability.md#slo"],
    ),
    (
        re.compile(r"ConnectionRefused|ECONNREFUSED|connection refused|no route to host", re.I),
        "connectivity_failure",
        0.82,
        "Service attempted to reach a host that refused the connection.",
        ["resilience.md#circuit-breaker"],
    ),
    (
        re.compile(r"KeyError|AttributeError|NullPointer|undefined is not|MissingField", re.I),
        "schema_drift",
        0.79,
        "Payload shape does not match the consumer contract.",
        ["contracts.md#strict-pydantic", "evolution.md#schema-versioning"],
    ),
    (
        re.compile(r"MemoryError|OOMKilled|out of memory|heap limit", re.I),
        "resource_exhaustion",
        0.88,
        "Process exceeded its memory allocation.",
        ["capacity.md#memory-budget"],
    ),
    (
        re.compile(r"PermissionError|403 Forbidden|AccessDenied|Unauthorized", re.I),
        "authorization_failure",
        0.84,
        "Credential or policy rejected the request.",
        ["security.md#least-privilege"],
    ),
]


def classify_root_cause(event: LogEvent) -> RootCause:
    haystack = f"{event.message}\n{event.stack_trace or ''}"
    for pattern, category, confidence, rationale, rules in _SIGNATURE_RULES:
        if pattern.search(haystack):
            return RootCause(
                category=category,
                confidence=confidence,
                rationale=rationale,
                steering_rules=rules,
            )
    return RootCause(
        category="unclassified",
        confidence=0.35,
        rationale="No known signature matched; escalated for human triage.",
        steering_rules=["triage.md#fallback"],
    )


def generate_fix_spec(event: LogEvent, root: RootCause) -> FixSpec:
    spec_id = f"spec-{uuid.uuid4().hex[:10]}"
    title = f"Auto-remediation: {root.category.replace('_', ' ')} in {event.service}"
    steps: list[str] = []
    patch_preview = ""
    estimated_impact = "medium"

    if root.category == "dependency_timeout":
        steps = [
            "Wrap the downstream call in a 3s async timeout.",
            "Enable circuit-breaker with 30s half-open probe.",
            "Emit an SLO breach event if p99 > 800ms over 5m.",
        ]
        patch_preview = (
            "@asyncio.timeout(3.0)\n"
            "async def call_upstream(payload):\n"
            "    return await client.post(UPSTREAM_URL, json=payload)\n"
        )
        estimated_impact = "high"
    elif root.category == "connectivity_failure":
        steps = [
            "Verify DNS resolution and TCP reachability to the upstream.",
            "Fail fast with bounded exponential backoff (250ms → 2s, max 5 attempts).",
            "Record circuit state in the /health payload.",
        ]
        patch_preview = (
            "for attempt in range(5):\n"
            "    try:\n"
            "        return await client.get(URL)\n"
            "    except ConnectionError:\n"
            "        await asyncio.sleep(min(2 ** attempt * 0.25, 2.0))\n"
        )
        estimated_impact = "high"
    elif root.category == "schema_drift":
        steps = [
            "Pin the payload to a strict Pydantic v2 model.",
            "Reject unknown fields in production, log in staging.",
            "Bump the schema version and publish a migration note.",
        ]
        patch_preview = (
            "class Payload(BaseModel):\n"
            "    model_config = ConfigDict(extra='forbid', strict=True)\n"
            "    id: UUID\n"
            "    kind: Literal['alpha', 'beta']\n"
        )
        estimated_impact = "medium"
    elif root.category == "resource_exhaustion":
        steps = [
            "Cap the in-memory buffer at 10k events (ring buffer).",
            "Stream to a disk-backed queue when over budget.",
            "Attach a cgroup memory limit and OOM alert rule.",
        ]
        patch_preview = (
            "from collections import deque\n"
            "buffer: deque[LogEvent] = deque(maxlen=10_000)\n"
        )
        estimated_impact = "high"
    elif root.category == "authorization_failure":
        steps = [
            "Rotate the credential and scope it to the minimum API.",
            "Add an audit log entry for denied calls.",
            "Alert on >5 denials/min per principal.",
        ]
        patch_preview = (
            "if not principal.has_scope('telemetry:write'):\n"
            "    raise HTTPException(status_code=403, detail='insufficient_scope')\n"
        )
        estimated_impact = "medium"
    else:
        steps = [
            "Route incident to the owning team via PagerDuty.",
            "Attach recent logs and the failing stack trace.",
            "Capture a minimal reproduction and open a ticket.",
        ]
        patch_preview = "# No automated patch — human review required.\n"
        estimated_impact = "low"

    return FixSpec(
        id=spec_id,
        title=title,
        root_cause=root,
        steps=steps,
        patch_preview=patch_preview,
        estimated_impact=estimated_impact,
    )


# ---------------------------------------------------------------------------
# WebSocket hub
# ---------------------------------------------------------------------------

class TelemetryHub:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    def count(self) -> int:
        return len(self._connections)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._connections:
            return
        text = json.dumps(payload, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)


hub = TelemetryHub()


# ---------------------------------------------------------------------------
# Agent pipeline
# ---------------------------------------------------------------------------

async def run_agent_pipeline(hub_ref: TelemetryHub, event_id: str, event: LogEvent) -> None:
    try:
        await hub_ref.broadcast({
            "type": "agent.stage",
            "event_id": event_id,
            "stage": "ingested",
            "detail": f"Received {event.severity.value.upper()} from {event.service}",
            "ts": _now_iso(),
        })
        await asyncio.sleep(0.35)

        root = classify_root_cause(event)
        await hub_ref.broadcast({
            "type": "agent.stage",
            "event_id": event_id,
            "stage": "classified",
            "detail": f"Root cause: {root.category} (confidence {root.confidence:.2f})",
            "root_cause": root.model_dump(),
            "ts": _now_iso(),
        })
        await asyncio.sleep(0.55)

        await hub_ref.broadcast({
            "type": "agent.stage",
            "event_id": event_id,
            "stage": "steering",
            "detail": f"Applying {len(root.steering_rules)} steering rule(s)",
            "rules": root.steering_rules,
            "ts": _now_iso(),
        })
        await asyncio.sleep(0.45)

        spec = generate_fix_spec(event, root)
        await hub_ref.broadcast({
            "type": "agent.result",
            "event_id": event_id,
            "detail": f"Fix spec {spec.id} generated",
            "spec": spec.model_dump(),
            "ts": _now_iso(),
        })
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("agent pipeline failed for %s", event_id)
        await hub_ref.broadcast({
            "type": "agent.stage",
            "event_id": event_id,
            "stage": "error",
            "detail": str(exc),
            "ts": _now_iso(),
        })


# ---------------------------------------------------------------------------
# Seeded incidents for the "Simulate" button
# ---------------------------------------------------------------------------

SIMULATED_INCIDENTS: list[LogEvent] = [
    LogEvent(
        service="checkout-api",
        severity=Severity.ERROR,
        message="Payment gateway timed out after 5000ms",
        stack_trace="Traceback (most recent call last):\n  ...\nTimeoutError: upstream deadline exceeded",
    ),
    LogEvent(
        service="orders-worker",
        severity=Severity.CRITICAL,
        message="KeyError: 'shipping_address' while building fulfillment payload",
        stack_trace="Traceback (most recent call last):\n  ...\nKeyError: 'shipping_address'",
    ),
    LogEvent(
        service="ingest-gateway",
        severity=Severity.ERROR,
        message="ConnectionRefused: redis://cache-01:6379",
    ),
    LogEvent(
        service="ml-scorer",
        severity=Severity.CRITICAL,
        message="OOMKilled while loading embeddings into memory",
    ),
    LogEvent(
        service="billing-cron",
        severity=Severity.ERROR,
        message="403 Forbidden: token lacks invoices:write scope",
    ),
]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

async def _metrics_loop(hub_ref: TelemetryHub) -> None:
    try:
        while True:
            await asyncio.sleep(5.0)
            await hub_ref.broadcast({
                "type": "metrics",
                "connections": hub_ref.count(),
                "ts": _now_iso(),
            })
    except asyncio.CancelledError:
        raise


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("starting kiro-sentry")
    task = asyncio.create_task(_metrics_loop(hub))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("stopped kiro-sentry")


app = FastAPI(title="Kiro-Sentry", version="1.0.0", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": app.version,
        "connections": hub.count(),
        "ts": _now_iso(),
    }


@app.get("/api/v1/meta")
async def meta() -> dict[str, Any]:
    return {
        "version": app.version,
        "steering": ACTIVE_STEERING_RULES,
        "hooks": ACTIVE_HOOKS,
    }


@app.post("/api/v1/telemetry/log", response_model=LogAccepted, status_code=202)
async def ingest_log(event: LogEvent) -> LogAccepted:
    event_id = f"evt-{uuid.uuid4().hex[:10]}"
    logger.info(
        "ingest %s service=%s severity=%s",
        event_id, event.service, event.severity.value,
    )
    await hub.broadcast({
        "type": "log",
        "event_id": event_id,
        "event": event.model_dump(mode="json"),
        "synthetic": False,
        "ts": _now_iso(),
    })
    asyncio.create_task(run_agent_pipeline(hub, event_id, event))
    return LogAccepted(
        accepted=True,
        event_id=event_id,
        received_at=datetime.now(timezone.utc),
    )


@app.post("/api/v1/simulate", response_model=LogAccepted, status_code=202)
async def simulate_anomaly() -> LogAccepted:
    event = random.choice(SIMULATED_INCIDENTS)
    event_id = f"evt-{uuid.uuid4().hex[:10]}"
    logger.info("simulate %s service=%s", event_id, event.service)
    await hub.broadcast({
        "type": "log",
        "event_id": event_id,
        "event": event.model_dump(mode="json"),
        "synthetic": True,
        "ts": _now_iso(),
    })
    asyncio.create_task(run_agent_pipeline(hub, event_id, event))
    return LogAccepted(
        accepted=True,
        event_id=event_id,
        received_at=datetime.now(timezone.utc),
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    try:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"version": app.version},
        )
    except TemplateNotFound:
        return HTMLResponse(
            "<!doctype html><html><body style='background:#0b0f19;color:#e5e7eb;"
            "font-family:system-ui;padding:2rem'>"
            "<h1>Kiro-Sentry</h1>"
            "<p>templates/index.html is pending (Task 3).</p>"
            "<p>API: <a style='color:#8b5cf6' href='/health'>/health</a> · "
            "<a style='color:#8b5cf6' href='/docs'>/docs</a></p>"
            "</body></html>"
        )


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_text(json.dumps({
            "type": "hello",
            "connections": hub.count(),
            "steering": ACTIVE_STEERING_RULES,
            "hooks": ACTIVE_HOOKS,
            "ts": _now_iso(),
        }))
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(json.dumps({"type": "pong", "ts": _now_iso()}))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws)
