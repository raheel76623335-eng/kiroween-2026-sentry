# Core Spec — Kiro-Sentry

## Objective
Ingest production telemetry, classify root cause, and emit a fix spec
end-to-end in under 2 seconds — with a live dashboard that shows every
stage of the AI remediation pipeline.

## User stories

### US-1 · Telemetry ingestion
As an SRE I can POST a log event to `/api/v1/telemetry/log` and receive
a 202 with an `event_id` that identifies the incident.

Acceptance criteria
- [x] Body validates against `LogEvent` (service, severity, message, optional stack_trace, meta).
- [x] Blank `service` returns 422.
- [x] Response includes `accepted`, `event_id`, `received_at`.

### US-2 · Live remediation stream
As an SRE I open the dashboard and see every log event and every agent
stage in real time over WebSocket `/ws/telemetry`.

Acceptance criteria
- [x] WS sends a `hello` frame on connect with active steering rules and hooks.
- [x] Log ingestion broadcasts a `log` frame.
- [x] Agent pipeline broadcasts `agent.stage` frames for `ingested`,
      `classified`, `steering`, and a final `agent.result` frame.

### US-3 · Anomaly simulation
As a demo viewer I click "Simulate System Anomaly" and watch a synthetic
incident travel through the pipeline.

Acceptance criteria
- [x] Button hits POST `/api/v1/simulate`.
- [x] A random incident archetype is chosen from the seeded set.
- [x] The pipeline runs end-to-end within 2 seconds.

### US-4 · Root cause classification
As an on-call engineer I get a categorized root cause with confidence
and the steering rules that were applied.

Acceptance criteria
- [x] Five signature categories recognized (timeout, connectivity, schema drift,
      resource exhaustion, authorization).
- [x] Unknown inputs fall back to `unclassified` with confidence < 0.5.
- [x] Every classification lists at least one steering rule.

### US-5 · Fix spec generation
As an on-call engineer I receive a concrete, copy-pasteable fix spec.

Acceptance criteria
- [x] Spec has id, title, root_cause, steps, patch_preview, estimated_impact.
- [x] `patch_preview` is real code, not a placeholder.
- [x] `unclassified` returns a human-escalation path.

## Non-goals
- Persistent storage (in-memory only for the demo).
- Multi-tenant authentication.
- Real LLM calls — the agent is deterministic for reproducibility.

## Kiro usage contract
- Steering lives in `.kiro/steering/tech.md`.
- This spec is the source of truth; drift is flagged by the `spec-sync` hook.
- Lint runs on every save; tests run on every commit.
