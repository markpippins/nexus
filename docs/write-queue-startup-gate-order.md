# Write-queue startup gate order (Option A — fail-fast, deployment-gated)

> Status: DRAFT for architect ruling — ruling thread `f63bfbc7`
> (issues-and-open-questions: "Ruling request: write_queue_reconciler
> startup semantics — retry-with-backoff vs fail-fast when
> NATS/stream unprovisioned"). This document is the engineer's
> recommended resolution (Option A) implemented as code; it is not
> authoritative until the architect's `type:decision` lands on that
> thread.

## The rule

The reconciler is **fail-fast at startup and reconnect-at-runtime**.
The deployment owns its prerequisites. Concretely, a correct write-queue
deployment performs these gates **in this order**:

| # | Gate | Enforced by | Failure mode |
|---|------|-------------|--------------|
| 1 | PROVISION — WRITE_QUEUE JetStream stream exists | `bin/ensure-write-queue-stream.py` (idempotent; refuses config drift loudly) | helper exits 1: unreachable NATS, memory-backed or mismatched existing stream |
| 2 | READINESS — NATS accepts client connections | real `nats.connect()` from the starter (single-arg form; `servers=` kwarg + positional URL raises) | retry loop up to `WRITE_QUEUE_GATE_TIMEOUT_S`, then refusal |
| 3 | PRE-EXISTENCE — `resolution.write_queue_applied` exists WITH its `write_id` PK | `to_regclass` + `pg_constraint` check | refuse before start; canonical DDL named |
| 4 | START — reconciler starts | `bin/start_write_queue_reconciler.py` execs the fail-fast reconciler | reconciler keeps its own fail-fast: exit 1 on NATS-unreachable, exit on missing stream, RuntimeError on missing staging table |
| 5 | ATTACH-WAIT — durable consumer attached | wrapper observes the reconciler's `durable consumer ... attached` log line | non-zero exit with log tail; "running" is never assumed |

Anything that reaches a steady running state has therefore passed every
gate. A prerequisite regression after start (e.g. NATS restarts with the
stream gone, PG dies) is handled by the reconciler's existing semantics:
runtime reconnect loop for transient NATS drops, loud exit for
structural loss of the stream, NAK-redelivery for PG hiccups (adjacent
gap: single `pg_conn` would NAK-loop on mid-run PG death — out of scope
here, tracked on the ruling thread).

## Empirical ground truth (scratch stack, 2026-09-25)

Scratch PG17-alpine + nats:2-alpine + python:3.12-slim, deps
nats-py==2.16.0 / psycopg2-binary==2.9.13 / asyncpg==0.31.0:

| Case | Behavior | Consequence |
|------|----------|-------------|
| T1 — NATS unreachable at startup | exit 1 (`NoServersError` after ~10s, ~112KB traceback spam) | fail-fast: no silent running |
| T2 — NATS up, stream missing | **exit 1** (`nats.js.errors.NotFoundError: find_stream_name_by_subject`) — exits, not a retry loop | provisioning is a prerequisite, not a reconciler concern |
| T3 — mid-run NATS disconnect | stays alive in nats-py reconnect loop; delivery resumes when server returns | runtime resilience without masking startup drift |
| Staging idempotency | `write_id` PK makes crash-loops safe; `ON CONFLICT (write_id)` upsert | at-least-once delivery is safe |
| Snapshot drift (PK missing) | reconciler's `ON CONFLICT (write_id)` insert fails loudly on first message | drift detection by design; pre-existence gate moves it earlier |

## Why fail-fast + gate order (not startup retry)

1. **A reconciler that runs without its stream is silent data loss.**
   Retry-with-backoff at startup would eventually "succeed" into a
   state nobody provisioned, and the intent backlog would sit unowned.
   Fail-fast surfaces drift at the deployment boundary, where it is
   someone's job.
2. **The empirical matrix shows fail-fast is already the code's
   behavior** (T1/T2 exit; only mid-run disconnects retry). Option A
   codifies reality instead of changing it — no behavior change, only
   named semantics and deployment ownership.
3. **Drift must be loud, never self-healed.** Assert-dont-create (PR
   #582) already refuses to conjure the staging table; the pre-existence
   gate applies the same posture before start, including the PK
   half-drift the reconciler only hits lazily on first message.
4. **CI already runs this order** — the wrapper makes the in-repo
   deployment match it.

## In-repo deployments of the gate order

- **CI boot-smoke** (`.github/workflows/nexus-core-image.yml`, job
  `boot-smoke`): provision (line ~237) → NATS readiness gate (line
  ~244) → reconciler start + attach-wait (lines ~268–282) → governed
  probe → postcondition. Build-only on push/PR; the full arc runs on
  `workflow_dispatch` with `boot_smoke=true`.
- **Titanium service unit** (`python/cascade/write-queue-reconciler.service`):
  `ExecStart` now points at `bin/start_write_queue_reconciler.py`
  (gates 1–5 enforced, then supervise). File change only in this
  branch — applying it to the live unit is a separate, explicit step.
- **Standalone**: `python3 bin/start_write_queue_reconciler.py
  --dry-run` runs gates 1–3 without starting anything.

## Producer vocabulary (Option A rider — engineer recommendation)

`WriteQueueProducer.enqueue` outcome strings:

- `queued` — JetStream accepted; the intent is durably held for
  reconciliation.
- `dropped_core_nats` (was `queued_core_nats`) — JetStream unavailable,
  core-NATS publish fired. **The publish succeeds at the server, but
  the reconciler consumes from the STREAM, not from a core
  subscription — no stream/consumer holds the intent; it reaches
  nothing durable.** The old name asserted a queueing that never
  happens; the new name says what actually happened. Behavior-preserving
  for callers: the REST layer keeps returning 202 with the outcome in
  the payload.
- `buffered_local` — NATS fully unreachable; JSONL fallback
  (`NEXUS_CORE_WRITEQUEUE_DIR`). Durability is local-only; **no drainer
  for this buffer exists anywhere in the repo** (the Option C gap —
  worth its own work item regardless of the ruling).
- `buffered_failed` — even the local buffer failed; the only
  genuinely-lost case, surfaced as HTTP 503.

The CI governed probe (`bin/ci_write_queue_probe.py`) now treats only
`queued` as persistence proof; `dropped_core_nats` fails the arc — it
is precisely the false-belief case the rename exists to expose.

## Records

- Ruling thread: `f63bfbc7` (options A/B/C; engineer recommends A)
- Empirical matrix: engineer assessment `5fecd811`
- R1 intent for this branch: `10a5bc0b`
- Adjacent gap (single pg_conn NAK-loop on mid-run PG death): noted on
  the ruling thread, explicitly out of scope here
