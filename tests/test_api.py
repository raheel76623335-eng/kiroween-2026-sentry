"""End-to-end tests for Kiro-Sentry."""

from fastapi.testclient import TestClient

from main import LogEvent, classify_root_cause, generate_fix_spec, app


def test_health_ok() -> None:
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["version"] == app.version


def test_meta_lists_steering_and_hooks() -> None:
    with TestClient(app) as client:
        r = client.get("/api/v1/meta")
        assert r.status_code == 200
        body = r.json()
        assert len(body["steering"]) >= 1
        assert len(body["hooks"]) >= 1


def test_ingest_log_accepts_valid_payload() -> None:
    with TestClient(app) as client:
        payload = {
            "service": "checkout-api",
            "severity": "error",
            "message": "Payment gateway timed out",
            "stack_trace": "TimeoutError: deadline exceeded",
        }
        r = client.post("/api/v1/telemetry/log", json=payload)
        assert r.status_code == 202
        body = r.json()
        assert body["accepted"] is True
        assert body["event_id"].startswith("evt-")


def test_ingest_rejects_blank_service() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/telemetry/log",
            json={"service": "   ", "message": "x"},
        )
        assert r.status_code == 422


def test_simulate_returns_event() -> None:
    with TestClient(app) as client:
        r = client.post("/api/v1/simulate")
        assert r.status_code == 202
        assert r.json()["event_id"].startswith("evt-")


def test_ws_receives_hello_and_pong() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/telemetry") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert "steering" in hello and "hooks" in hello
            ws.send_text("ping")
            pong = ws.receive_json()
            assert pong["type"] == "pong"


def test_ws_receives_streamed_log_and_stage_on_simulate() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/telemetry") as ws:
            ws.receive_json()  # hello
            r = client.post("/api/v1/simulate")
            assert r.status_code == 202
            first = ws.receive_json()
            assert first["type"] == "log"
            assert "event" in first
            second = ws.receive_json()
            assert second["type"] == "agent.stage"
            assert second["stage"] == "ingested"


def test_classifier_timeout() -> None:
    ev = LogEvent(service="s", message="boom", stack_trace="TimeoutError: x")
    rc = classify_root_cause(ev)
    assert rc.category == "dependency_timeout"
    assert rc.confidence > 0.8
    assert rc.steering_rules


def test_classifier_unknown_falls_back() -> None:
    ev = LogEvent(service="s", message="mystery event")
    rc = classify_root_cause(ev)
    assert rc.category == "unclassified"
    assert rc.confidence < 0.5


def test_fix_spec_schema_drift_has_patch() -> None:
    ev = LogEvent(service="s", message="m", stack_trace="KeyError: 'x'")
    rc = classify_root_cause(ev)
    spec = generate_fix_spec(ev, rc)
    assert spec.id.startswith("spec-")
    assert spec.root_cause.category == "schema_drift"
    assert spec.steps
    assert "ConfigDict" in spec.patch_preview
