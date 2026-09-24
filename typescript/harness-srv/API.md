# harness-srv — Generic Execution Harness

> Port: **3420**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Merges Tackle role context (prompt + tool ACL + procedure cards) with Wind task context (inputs + acceptance criteria) and invokes an agent via the configured harness.

**8 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | GET /health |
| GET | `/jobs/:jobId` | GET /jobs/:jobId — job status + partial output (P1 item 6) The async contract's read path: state envelope with the RAW accumulated stdout (partial while running — preserved on timeout/failure), stderr, and exact exit/timeout metadata. 404 for unknown jobs. |
| GET | `/jobs/:jobId/events` | GET /jobs/:jobId/events?after=<seq> — replayable SSE job stream Typed envelopes translated once at the boundary from the opencode JSON event stream: job.accepted, job.started, text.delta, thinking, and the terminal job.completed / job.failed / job.timed_out / job.cancelled. Replays seq > after on co |
| POST | `/jobs/:jobId/interrupt` | POST /jobs/:jobId/interrupt — SIGTERM the child, cancel the job The async contract's cancellation path: kills the opencode child by PID (same mechanism as the runaway watchdog) and marks the job cancelled so the subscriber can surface the interrupt instead of a timeout. |
| POST | `/resolve-context` |  |
| POST | `/run` |  |
| POST | `/run-direct` |  |
| GET | `/sessions` | GET /sessions — active session list (runaway watchdog visibility) |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->

---

# harness-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3420`. JSON in/out. Execution harness: resolves Tackle role
> context + Wind task context, then invokes an agent (opencode CLI or ollama
> HTTP) and reports the result. Every run emits `harness.started` →
> `harness.completed|failed` events to `cascade.events` and governance receipts
> (`PLAN_CREATE` → `IMPLEMENTATION` → `REVIEW_PASS|REVIEW_REJECT|BLOCK`).

## Run envelope (POST /run)

Request body:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `wind_task_id` | string | **yes** | — | `wind.tasks.id` to execute. |
| `context` | object | no | — | Overrides merged into the task's `input_spec`. |
| `work_dir` | string | no | `/home/codex/dev` | Working directory override. |
| `harness_id` | string | no | role default | Harness override (default `harn-opencode`). |
| `agent` | string | no | role name | Agent name override (opencode). |
| `timeout_ms` | number | no | `300000` | Execution timeout. |
| `resolve_only` | bool | no | `false` | Skip execution; return resolved context only. |

Response — **200** (`job_id` + task + outcome + raw output):

```json
{
  "job_id": "<uuid>", "role": "engineer",
  "task": { "wind_task_id": "…", "wind_task_name": "…", "task_slug": "…", "scope": "…" },
  "outcomes": [ { "code": "…", "description": "…" } ],
  "outcome": { "code": "…", "id": "…", "confidence": "exact|fuzzy|keyword" } | null,
  "prompt_preview": "…", "harness_id": "…", "exit_code": 0,
  "stdout": "…", "stderr": "…", "duration_ms": 1234,
  "events": { "started": "<event_id>" }
}
```

Errors: **400** (`wind_task_id is required`; INTERACTIVE-hosted role refused),
**403** (`admission denied` + `admission: {outcome, reason}`), **500**
(`{job_id, error, duration_ms}`).

## Direct run envelope (POST /run-direct)

For interactive turns where the caller already assembled the full prompt
(bypasses `wind.tasks` lookup). Request body:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `role` | string | **yes** | — | Agent role (maps to `tackle.config_bundle`). |
| `prompt` | string | **yes** | — | Full prompt text. |
| `model` | string | no | role default | opencode-format model id override. |
| `work_dir` | string | no | `/home/codex/dev` | Working directory. |
| `agent` | string | no | role name | Agent name override. |
| `timeout_ms` | number | no | `600000` | Execution timeout. |
| `channel` | string | no | `duality` | Invocation channel (labels the source in events). |

Response — **200**: `{ job_id, role, exit_code, stdout, stderr, duration_ms, prompt_preview, harness_id, model, events: { started } }`.

Errors: **400** (`role is required` / `prompt is required` / INTERACTIVE-hosted / no active config_bundle), **403** admission denied, **500**.

## Context envelope (POST /resolve-context)

Request: `{ "wind_task_id": "…" }` (**required**). Response — **200**:
`{ role, task, prompt_length, prompt_preview, procedure_cards, harness_id, tool_acl }`.
Errors: **400** missing `wind_task_id`, **500**.

## Session envelope (GET /sessions)

Active-run visibility for the runaway watchdog. Response — **200**:

```json
{ "sessions": [ { "jobId": "…", "role": "…", "model": "…", "startedAt": "<ISO>", "elapsedSeconds": 12 } ], "count": 0 }
```

## Health (GET /health)

**200** `{ status: "ok", port: 3420, uptime }` · **503** `{ status: "error", error }` (DB or Redis unreachable).

## Notes

- Exit codes: `0` success; `124` timeout; `137` killed by signal (watchdog/external).
- Runs are watched by a 15-minute runaway watchdog: a session producing no
  agent records is SIGTERM'd, its model unloaded, and a `type:runaway-detected`
  record + `BLOCK` governance receipt are emitted.
- Admission (T20): roles without a valid `tackle.config_bundle` are refused
  (`403`) with `admission.outcome` ∈ `NO_CONFIG | ROLE_REVOKED | CONFIG_INVALIDATED`.
- Governance receipts land in `peb.governance_events` via the canonical receipt chain.
