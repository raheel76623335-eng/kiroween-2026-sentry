# Steering — Tech Stack & Conventions

## Runtime
- Python 3.11, FastAPI (async), Pydantic v2 (`model_config = ConfigDict(...)`).
- Uvicorn with `--proxy-headers`. Never enable `--reload` in production.
- Frontend is a single static HTML file — no build step. Tailwind via CDN.

## Code style
- Line length 100, `flake8` clean. Type hints everywhere.
- No `TODO` comments in committed code. No placeholder `pass` blocks.
- All external I/O is `async`. No blocking calls on the event loop.

## API conventions
- All JSON responses use Pydantic models.
- Errors return `{"detail": "..."}` with correct HTTP status.
- Ingestion endpoints return `202 Accepted` with an `event_id`.

## Logging
- Structured logs only. Never log secrets, tokens, or PII.
- Every log line includes `service`, `severity`, `event_id`.

## Testing
- `pytest` + `fastapi.testclient.TestClient`.
- Every public endpoint has at least one happy-path and one failure-path test.
- Agent behaviour is deterministic — no network, no randomness in tests.
