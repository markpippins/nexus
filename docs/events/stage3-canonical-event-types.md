# Events — Stage 3: Canonical Event Types (waves 1–3: twelve system families)

> **Pipeline position:** v2 inventory (`events-inventory-v2-raw-observations.md`)
> cataloged raw occurrences (stages 1–2). This doc is **stage 3** for the first
> wave: collapse occurrences into canonical event types and decide
> publish-worthiness + destination (stage 4), per system. Worthiness is still a
> recommendation, not an implementation order.
>
> **Scope (waves):**
> - **Wave 1** — the five gap systems: `tackle.agent_scheduler`
>   (scheduler ticks/launches/skips), `timeclock` (`tackle.agent_timeclock`),
>   `nebula.agent_records`, `harness-srv` (run lifecycle, INTERACTIVE guard,
>   watchdog), `tackle.role_leases` (role-lease dispenser, nebula-srv routes).
> - **Wave 2** — `registry.status_events`, voyager, execution drift-kinds,
>   circuit-breaker trips, substance segment expiry (sections 6–10).
> - **Wave 3** — the MCP tool-server family (discovery/invocation/failure/
>   lifecycle) and WRP core (identity, address, state-DAG, kernel, arbitration)
>   (sections 11–12).

## Conventions

**Event type naming** — dotted lowercase `domain.entity.verb_past_tense`,
matching the measured vocabulary: `harvest.captured`, `candidate.discovered`,
`harness.started`, `wr.submitted`, `intent_record.created`.

**NATS subject** — `nexus.<domain>.v1.<event_type>` (matches existing:
`nexus.kernel.v1.transition.*`, `nexus.fs.v1.*`, cascade fallback
`nexus.cascade.v1.*`).

**Envelope** — reuse the `cascade.events` column set as the canonical envelope:

```
event_id (uuid) · event_type · event_timestamp · source · payload (jsonb)
aggregate_type · aggregate_id · actor_type · actor_id
correlation_id · causation_id · caused_by_event_type · sequence_number
```

**Bus destination** — `cascade.events` is the generic workflow bus (its
aggregate_type/aggregate_id columns are generic, so non-WR aggregates fit).

---

## 1. tackle.agent_scheduler → cascade.events (subject `nexus.tackle.v1.scheduler.*`)

Table: `tackle.agent_scheduler` (role, task_slug, schedule_type
interval|cron|manual|event, cron_expr, event_criteria jsonb, enabled,
last_run_at, last_run_status).

| # | Raw occurrence (v2 §9) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Tick cycle evaluated (`evaluate_tick` → summary) | `scheduler.tick` | **No** (too hot; fold into outcomes) | n/a |
| 2 | Entry became due (cron match / interval elapsed / event-criteria match) | `scheduler.entry.due` | **Yes** | entry {id, role, task_slug, schedule_type, cron_expr / interval / event_criteria}, event_ids (for type=event) |
| 3 | Agent launched → `{status:'launched', pid}` | `scheduler.agent.launched` | **Yes** | entry, pid, model_id, harness |
| 4 | Launch failed (binary not found / exception) | `scheduler.agent.launch_failed` | **Yes** | entry, error |
| 5 | Skip — role is INTERACTIVE-hosted (harn/freebuff guard) | `scheduler.skip.interactive_hosted` | **Yes** | entry, role |
| 6 | Skip — no eligible work for role | `scheduler.skip.no_work` | **Yes** | entry, role |
| 7 | Skip — pipeline empty (no enabled entries) | `scheduler.skip.empty_pipeline` | **Yes** | count |
| 8 | Event-scheduled entry consumed matching events | `scheduler.event.consumed` | No (fold into #2) | event_ids |
| 9 | Cron expression unparseable (ValueError) | `scheduler.cron.invalid` | **Yes** (error/config drift) | entry, cron_expr, error |

Aggregate: `aggregate_type='agent_scheduler_entry'`, `aggregate_id=entry.id`.
Notes: #5–#7 are the shadow-mode guard outcomes (emptiness check + interactive
skip) that wr-conf work already exercised — these are the observability win.
#4/#9 are the error surface.

---

## 2. timeclock → cascade.events (subject `nexus.timeclock.v1.*`)

Table: `tackle.agent_timeclock` (role, model, session_id, clock_in, clock_out,
status, valid_from/until).

| # | Raw occurrence (v2 §17) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Clock-in success (`clock_in` route) | `timeclock.clocked_in` | **Yes** (R13 compliance/audit) | role, model, session_id, clock_in |
| 2 | Clock-out success | `timeclock.clocked_out` | **Yes** | role, model, session_id, clock_out, duration_s |
| 3 | Heartbeat success | `timeclock.heartbeat` | No by default (suppressible) | role, model, session_id |
| 4 | Session timed out by cleanup (`timeout_cleanup` → rowcount) | `timeclock.session.timed_out` | **Yes** | role, model, session_id, timeout_minutes |
| 5 | Clock-out / heartbeat for unknown session (HTTPException) | `timeclock.session.unknown` | **Yes** (error) | role, model, session_id?, error |

Aggregate: `aggregate_type='timeclock_session'`, `aggregate_id=session_id`.
Notes: #1/#2 are the natural evidence for R13 (clock in/out governance).

---

## 3. nebula.agent_records → cascade.events (subject `nexus.nebula.v1.agent_record.*`)

Table: `nebula.agent_records` (+ `_history` projection; record_type CHECK:
report, analysis, assessment, inspection, prompt, response, engineering_log,
architecture_note, decision).

| # | Raw occurrence (v2 §12) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Agent record created | `nebula.agent_record.created` | **Yes** — the routing/inbox signal | record_id, recordType, role, level, visibilityScope, tags, plan_ref |
| 2 | Agent record updated | `nebula.agent_record.updated` | **Yes** (lower volume) | record_id, changed fields |
| 3 | Record rejected — invalid recordType/level (400) | `nebula.agent_record.rejected` | **Yes** (error) | role, reason |

Aggregate: `aggregate_type='agent_record'`, `aggregate_id=record_id`.
Notes: #1 is the event that can drive cross-role notification (e.g. watchdog
hearing `to:watchdog` findings without polling) — the inbox stays the durable
query surface, this becomes the push signal.

---

## 4. harness-srv → cascade.events (subject `nexus.harness.v1.run.*`)

Channel already wired: harness-srv writes `cascade.events` directly; the
vocabulary `harness.started|failed|completed` already exists (measured).

| # | Raw occurrence (v2 Projection) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | `/run` accepted, spawn started (child PID tracked) | `harness.run.started` | **Yes** (exists — keep) | session_id, bundle_id, pid, role |
| 2 | Run completed (+ governance receipt) | `harness.run.completed` | **Yes** (exists — keep) | session_id, receipt, summary |
| 3 | Run failed | `harness.run.failed` | **Yes** (exists — keep) | session_id, error |
| 4 | **`/run` refused 400 — INTERACTIVE guard** (interactive bundle with lease / unbounded consumer) | `harness.run.refused` | **Yes — NEW (the gap)** | session_id, bundle_id, role, reason (interactive_with_lease | unbounded_consumer) |
| 5 | **Watchdog triggered → SIGTERM kill + unload + record** | `harness.run.watchdog_killed` | **Yes — NEW (the gap)** | session_id, pid, timeout_ms, exit_signal |
| 6 | Session ended | `harness.session.ended` | Yes | session_id, duration_s |
| 7 | `/resolve-context` served | `harness.context.resolved` | No (mechanical) | session_id |
| 8 | `/run` requested (raw) | `harness.run.requested` | No (redundant with started) | n/a |

Aggregate: `aggregate_type='harness_run'`, `aggregate_id=session_id`.
Notes: #4 and #5 are the two events wr-conf-003/004/005 guard but no channel
records today. #1–#3 must be emitted by the spawn path (not just the
execFileAsync legacy path).

---

## 5. tackle.role_leases (dispenser) → cascade.events (subject `nexus.tackle.v1.lease.*`)

Dispenser API (nebula-srv routes.ts): `POST /api/role-leases/issue`,
`POST /api/role-leases/:id/renew`, `POST /api/role-leases/:id/revoke`,
`POST /api/role-leases/consume`, `GET /api/role-leases?role=`,
`GET /api/role-leases/stale`.
Table: `tackle.role_leases` (role, **channel** [freebuff | execution_worker |
harness/wind], model, window_start/end, budget_units, consumed_units, status
ACTIVE|EXPIRED|RELEASED, acquired_at, expires_at, released_at).

| # | Raw occurrence (v2 §9/§11 + dispenser) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Lease issued | `lease.issued` | **Yes** | lease_id, role, channel, model, budget_units, window_start/end |
| 2 | Lease renewed (window + budget) | `lease.renewed` | Yes | lease_id, new window, new budget |
| 3 | Lease revoked (explicit) | `lease.revoked` | **Yes** | lease_id, role, reason |
| 4 | **Consume — `consumed_units += 1` (work.completed → lease accounting)** | `lease.consumed` | **Yes — the canonical accounting event** | lease_id, role, channel, consumed_units, remaining, work_ref (session/work unit id) |
| 5 | Window expired (expiry sweep) | `lease.expired` | **Yes** | lease_id, role, channel |
| 6 | **Budget exhausted (consumed ≥ budget → STOP + surface)** | `lease.exhausted` | **Yes — the STOP signal** | lease_id, role, consumed_units, budget_units |
| 7 | Stale lease detected (past window, still ACTIVE — sweep check #5) | `lease.stale` | **Yes** | lease_id, role, channel, window_end |
| 8 | Consume denied (no active lease / wrong channel) | `lease.consume_denied` | **Yes** (error) | lease_id?, role, reason |

Aggregate: `aggregate_type='role_lease'`, `aggregate_id=lease_id`.
Notes: #4 is the event the whole accounting discussion pointed at — one
canonical `work.completed → lease accounting` event regardless of which
execution channel (execution_worker / harness-wind / interactive) produced the
completion. #6 is the budget-exhaustion → STOP + surface-to-operator hook. #7
feeds the pipeline-health-sweep drift signal.

---

## 6. registry / heartbeat / service-broker → registry.status_events (subject `nexus.registry.v1.service.*`)

Table already stores the transition vocabulary: `registry.status_events`
(service_name, old_state, new_state, reason, response_time_ms, error_message,
changed_at). The gap is **emitters** — `heartbeat.py` (`_send_once` →
False|True), the health-monitor scripts, and service-broker-mcp (`ok` |
`FAILURE`).

| # | Raw occurrence (v2 §18) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Heartbeat sender registered/started | `registry.service.registered` | Yes | service_name, service_id, url, interval |
| 2 | Status transition observed (old → new) | `registry.service.status_changed` | **Yes — the table's native row** | service_name, old_state, new_state, reason, response_time_ms |
| 3 | Health check failed / timed out | `registry.service.health_check_failed` | Yes | service_name, error_message, response_time_ms |
| 4 | Heartbeat beat sent | `registry.service.heartbeat` | No by default (too hot) | service_name |
| 5 | Consecutive-failure threshold crossed / recovery threshold crossed | `registry.service.threshold_crossed` | Yes *(derived)* | service_name, threshold_kind (failures | recovery), count |
| 6 | Repeated up→down→up within window | `registry.service.flap_detected` | Yes *(derived)* | service_name, window, transitions |
| 7 | Broker call outcome | `service_broker.call_failed` | Yes | service, method, status (FAILURE) |

Notes: #5/#6 are **derived** — stage-4+ consumers computed over #2/#3, listed
here so the raw base guarantees they're computable. #2/#3 are the raw
occurrences every monitor script already produces; wiring them to
`registry.status_events` closes the gap.

## 7. voyager → domain tables + cascade.events (subject `nexus.fs.v1.*`)

Domain storage already exists (`voyager.file_observation`, `directory_observation`,
`scan_epoch`, `metadata_span`, `observation_edge_hint`, `entity_drift`,
`topology_signal`, `identity_candidate`, `entity`). These are the event stores;
the bus events are the cross-system push signals.

| # | Raw occurrence (v2 §19) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Epoch started / completed / failed | `voyager.epoch.started` · `voyager.epoch.completed` · `voyager.epoch.failed` | Yes | epoch_id, root_path, files_scanned, new_files, cached_files, errors_count, error |
| 2 | File observed (new/modified) | `voyager.file.observed` | Yes (sampled) | path, size, mtime, inode, device_id, observation_id |
| 3 | File missing during scan (FileNotFoundError) | `voyager.file.missing` | Yes (error) | path, epoch_id |
| 4 | Drift detected (fingerprint diff) | `voyager.drift.detected` | **Yes — the signal** | entity, old_fingerprint, new_fingerprint, magnitude (MAJOR|MASSIVE|MINOR|TRACE) |
| 5 | Edge hint emitted | `voyager.edge_hint.emitted` | Yes (sampled) | from_obs_id, to_obs_id, evidence_type, evidence_confidence |
| 6 | Topology signal emitted | `voyager.topology.signal` | Yes | signal_id, epoch_id, structure_type, structure_scope, observation_ids |
| 7 | Emission rejected by validator (ValueError) | `voyager.observation.rejected` | Yes (error) | reason |
| 8 | Canonical asset upserted (semantics adapter) | `voyager.asset.upserted` | Yes | asset_id, revision_id, content_hash |
| 9 | Metadata span emitted | `voyager.span.emitted` | No (metadata_span is the store) | span_id, text, span_type |

Notes: #4 has a home table (`entity_drift`) but no published signal — it's the
filesystem-level equivalent of execution's drift-kinds. Subject stays
`nexus.fs.v1.*` per the existing `voyager_envelope_adapter.subject_for` map.

## 8. execution drift-kinds → cascade.events (subject `nexus.execution.v1.drift.*`)

Already half-cooked: `execution-srv` embeds **eight drift-kind constants**
(v2 §11) produced by its sweep. Collapse to one type + `kind` discriminator so
consumers filter uniformly; the sweep findings are the natural payload.

| # | Raw occurrence (v2 §11) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Lease drift — unreleased lease for terminal request | `execution.drift.detected` (kind=`unreleased_lease_for_terminal_request`) | Yes | kind, request_id, lease_id |
| 2 | Stale active lease | `execution.drift.detected` (kind=`stale_active_lease`) | Yes | kind, lease_id, expires_at |
| 3 | Receipt/request mismatch | `execution.drift.detected` (kind=`receipt_request_mismatch`) | Yes | kind, receipt_id, request_id |
| 4 | Receipt/attempt mismatch | `execution.drift.detected` (kind=`receipt_attempt_mismatch`) | Yes | kind, receipt_id, attempt_id |
| 5 | Orphan lease request mismatch | `execution.drift.detected` (kind=`orphan_lease_request_mismatch`) | Yes | kind, lease_id, request_id |
| 6 | Attempt status diverges from request | `execution.drift.detected` (kind=`attempt_status_diverges_from_request`) | Yes | kind, attempt_id, request_id |
| 7 | Attempt orphan with no lease | `execution.drift.detected` (kind=`attempt_orphan_no_lease`) | Yes | kind, attempt_id |
| 8 | Attempted but never completed | `execution.drift.detected` (kind=`attempted_no_completion`) | Yes | kind, request_id |
| 9 | DB guard rejected write (attempt/lease consistency, receipts immutable) | `execution.consistency.rejected` | Yes (error) | table, reason |

Notes: #1–#8 are one canonical type (`execution.drift.detected`) with a `kind`
payload — the alternative (8 distinct types) trades filter simplicity for
registry bloat. #9 captures the BEFORE-trigger rejections (v2 §0) which are
currently silent in the event layer.

## 9. circuit-breaker → cascade.events (subject `nexus.conduit.v1.circuit.*`)

Tables: `conduit.circuit_breaker`, `conduit.role_circuit_breaker`,
`peb.role_circuit_breaker`, `tackle.circuit_breaker` (tripped, tripped_at,
retry_after, error, detail, source, paused, wake_requested_at, max_fallbacks).
Triggered by: conduit app routes (`trip_breaker`/`reset_breaker`/`pause_conduit`/
`resume_conduit`), conduit-mcp routes (`/circuit-breaker/trip|reset`,
`/conduit/pause|resume`), `db_adapter.trip_and_requeue`.

| # | Raw occurrence (v2 §4/§5) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Breaker tripped (auto or manual) | `circuit_breaker.tripped` | **Yes** | scope (global | role), role?, tripped_at, retry_after, error, detail, source |
| 2 | Breaker reset | `circuit_breaker.reset` | Yes | scope, role?, reset_by |
| 3 | Plan requeued on trip (`trip_and_requeue`) | `work.requeued` | Yes | plan_id, role, session_id, error, source |
| 4 | Conduit paused | `conduit.paused` | Yes | timestamp, by |
| 5 | Conduit resumed | `conduit.resumed` | Yes | timestamp, by |
| 6 | Role circuit tripped / reset | `circuit_breaker.role_tripped` · `circuit_breaker.role_reset` | Yes | role, retry_after |

Notes: #3 is the observable downstream of a trip — without it, trips look like
stalls. #1/#2 with `scope` covers both global and role-scoped breakers across
conduit/tackle/peb.

## 10. substance segment expiry → cascade.events (subject `nexus.substance.v1.segment.*`)

Occurrence today: `trg_segment_expired` / `trg_member_expired` fire NOTIFY on
`nebula.segments_history` / `nebula.segment_set_members`; `substance.listener`
consumes and **drops it** (logs only). The re-emission is the gap.

| # | Raw occurrence (v2 §22) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Segment expired (NOTIFY received) | `substance.segment.expired` | **Yes — the re-emission** | segment_id, segment_set_id(s) |
| 2 | Segment-set member expired | `substance.member.expired` | Yes | segment_set_id, member_id |
| 3 | Segment set created / updated / deleted | `substance.segment_set.created` · `.updated` · `.deleted` | Yes (low volume) | segment_set_id, name, metadata |
| 4 | Domain segment linked / unlinked | `substance.segment.linked` · `.unlinked` | No (mechanical) | domain_type, domain_id, segment_set_id |

Notes: #1/#2 are the raw occurrences currently vanishing inside the listener —
this closes the consumption/re-emission loop. #3 gives cross-system
visibility into the segment-set catalog.

---

## 11. MCP tool-server family → cascade.events (subject `nexus.mcp.v1.<server>.*`)

~20 MCP servers/clients in `typescript/` share one observable shape: GET
`/health` · POST `/` (JSON-RPC: initialize / tools/list / tools/call) · status
400/503/500. Per-server measured specifics in v2 §26 (tools-aggregator init
outcomes, slash-command DSL error classes, image phases, file-system UP,
address-tts/ui-tools `unreachable`, service-broker `FAILURE`, assembly-
/semantics-mcp bridges). One generic vocabulary with a `server` discriminator:

### 11a. Generic tool-server lifecycle + invocation

| # | Raw occurrence (v2 §26) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Server started / health OK | `mcp.server.started` | Yes | server, version |
| 2 | Server health unreachable / degraded (status unreachable/error, 503) | `mcp.server.unreachable` | **Yes** | server, status, error |
| 3 | Server stopped / deregistered | `mcp.server.stopped` | Yes | server |
| 4 | Config changed (config/ai: bundles, harnesses, models, providers, roles) | `mcp.server.config_changed` | Yes | server, resource, change |
| 5 | Tool list requested / returned / metadata read | `mcp.tools.list_requested` · `mcp.tools.list_returned` · `mcp.tool.metadata_read` | No (mechanical) | server |
| 6 | Call received / arguments parsed | `mcp.call.received` · `mcp.call.arguments_parsed` | No (too hot) | server, tool |
| 7 | Arguments invalid (CoercionError / DslParseError) | `mcp.call.arguments_invalid` | **Yes** (error) | server, tool, error_class |
| 8 | Authorization denied | `mcp.call.unauthorized` | **Yes** (error) | server, tool |
| 9 | Call dispatched / started | `mcp.call.started` | No by default | server, tool |
| 10 | Call completed (result serialized + returned) | `mcp.call.completed` | Yes (sampled) | server, tool, duration_ms |
| 11 | Call failed (server exception; DispatchError; `FAILURE`) | `mcp.call.failed` | **Yes** | server, tool, error_class |
| 12 | Call timed out | `mcp.call.timed_out` | **Yes** | server, tool, timeout_ms |
| 13 | Tool not found (unknown tool) | `mcp.call.tool_not_found` | **Yes** | server, tool |
| 14 | Dependency/transport failure (upstream unreachable) | `mcp.call.transport_failed` | **Yes** | server, tool, error |

### 11b. Per-server specifics (mapped to 11a)

| Server | Measured fact (v2 §26) | Maps to |
|---|---|---|
| tools-aggregator | `POST /init` → initialized \| initialization_failed | `mcp.aggregator.initialized` · `mcp.aggregator.init_failed` (Yes) |
| tools-aggregator | `/registry`, `/tools/by-service/:service` read | no-publish (mechanical) |
| slash-command-mcp | DslParseError×5, CoercionError×12, DispatchError×4 | `mcp.call.arguments_invalid` · `mcp.call.failed` |
| image-server | phases evaluation \| source, status UP/DOWN | `phase` payload key on call events |
| file-system-server / secure | status UP, Error×11 | `mcp.call.failed` |
| address-tts-mcp / ui-tools-mcp | status ok \| unreachable \| error | `mcp.server.unreachable` |
| service-broker-mcp | status ok \| FAILURE | `mcp.call.failed` |
| assembly-mcp / semantics-mcp | bridges (forum-agenda, post-artifact, supporting-refs, move-thread) | `mcp.call.completed` / `mcp.call.failed` with `bridge` payload key |

Notes: calls are hot — terminal outcomes (#10–#14) publish, mechanics
(#5/#6/#9) default no. This is the family-wide vocabulary chat outlined; each
server fills in `server` + tool-specific payload.

## 12. WRP core → cascade.events (subject `nexus.wrp.v1.*`)

`python/nexus_core/wrp` + `python/ir` (v2 §2). Pure functions default
no-publish; error outcomes and the ledger write publish.

| # | Raw occurrence (v2 §2) | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Intent normalized (action, target) | `wrp.intent.normalized` | No (mechanical) | action, target |
| 2 | Intent rejected — empty action (ValueError) | `wrp.intent.rejected` | **Yes** (error) | intent, reason |
| 3 | Identity derived → (entity_key, 'event', scope) | `wrp.identity.derived` | **Yes** | entity_key, scope |
| 4 | Identity missing — no action in intent (ValueError) | `wrp.identity.missing` | **Yes** (error) | intent, reason |
| 5 | Identity matched (strong/medium/none) | `wrp.identity.matched` | Yes (sampled) | entity_key, match_strength |
| 6 | Ambiguous / conflicting identity | No — not produced by `wrp/identity.py` yet; resolution vocabulary is `semantics.asset_identity_claim` (identity, supersession, derivation, consolidation, split) + claim status open/resolved/rejected | |
| 7 | Address minted (`make_address` → cal://) | `wrp.address.minted` | No (mechanical) | address |
| 8 | Address parsed | `wrp.address.parsed` | No | address |
| 9 | Address unparseable (`parse_address` → None) | `wrp.address.unparseable` | **Yes** (error) | address, reason |
| 10 | Content hash computed (`content_hash` sha256[:12]) | `wrp.hash.computed` | No (pure function) | hash |
| 11 | State version committed (`state_dag.mutate` → version) | `wrp.state.mutated` | Yes (sampled — the ledger write) | version_id, parents, source_event_id, edge_type |
| 12 | Mutation rejected (invalid delta/heads — ValueError) | `wrp.state.mutation_rejected` | **Yes** (error) | reason |
| 13 | Transition invalid (`states.is_valid_transition` False) | `wrp.state.transition_invalid` | **Yes** (error) | from_state, to_state |
| 14 | Kernel delta applied | `wrp.kernel.delta_applied` | No (kernel_delta_log is the store) | version |
| 15 | Kernel invariant violated (INVARIANT_VIOLATION) | `wrp.kernel.invariant_violated` | **Yes** (error) | version, error |
| 16 | Kernel validation error (VALIDATION_ERROR) | `wrp.kernel.validation_error` | **Yes** (error) | version, error |
| 17 | Replay fingerprint mismatch (`byte_identical_replay`) | `wrp.replay.mismatch` | **Yes** (error) | fingerprint_1, fingerprint_2, deltas |
| 18 | Projection reduced (abstraction level L1–L4) | `wrp.projection.reduced` | No (mechanical) | plan_id, level, scope |
| 19 | Lease arbitration selected / no match / preempted / consolidated (`ir/` lease pool) | `wrp.arbitration.selected` · `wrp.arbitration.no_match` · `wrp.lease.preempted` · `wrp.lease.consolidated` | Yes (sampled) | event, lease_id, score |

Notes: #19 is the in-memory `ir/` arbitration machinery — same family as the
wave-1 `lease.*` types (channel='wrp-arbitration'); preemption/consolidation
are the governance observables (SOCO rules land here). #6 is deliberately
marked no-publish until a resolver produces it — the semantics claim
vocabulary is the target.

---

## 13. absorb → cascade.events (subject `nexus.absorb.v1.*`)

Ingest (absorb) run lifecycle — plan 0003. Emitter: `python/absorb/absorb/emitter.py`
publishes to NATS (`nexus.absorb.v1.<event_type>`, shared `CanonicalEnvelope`);
`bus_mirror.py` projects into `cascade.events` (idempotent `ON CONFLICT (event_id) DO NOTHING`).
Envelope columns: `aggregate_type`/`aggregate_id` travel in the envelope payload;
`correlation_id` = batch/run id; `causation_id` chains step-to-step.

| # | Lifecycle occurrence | Canonical event type | Publish? | Payload keys |
|---|---|---|---|---|
| 1 | Batch run begins | `run.started` | **Yes** | profile_id, version, planned_count |
| 2 | A source completed (content-hash aggregated) | `source.completed` | **Yes** | run_id, source_hash, watermark |
| 3 | A step failed (C1 taxonomy) | `step.failed` | **Yes** | run_id, step, error_code, retryable |
| 4 | Batch run ends (terminal outcome) | `run.completed` | **Yes** | counts, warnings, policy_skips |

Registered per AC2 of plan 0003. No step-level completion events (deferred —
~7k events for the full corpus ruled excessive). Publish is best-effort and
failure-isolated (AC4); sent/dropped counters surface in the run-summary
warnings.

---

## Next wave (same method, not yet collapsed)

- `bin/` operational script surface (v2 §28)
- Assembly (post/comment/thread outcomes) and the nebula knowledge graph
  (embedding / cross-ref / reconcile outcomes)
- vision/losm orchestration outcomes — only if a consumer demands them

**Decision rule carried forward:** collapse first, judge second. An occurrence
becomes a canonical type if it (a) is observable by at least one other system,
(b) is a terminal outcome (completed/failed/refused/exhausted) or a
cross-cutting transition, or (c) is an error condition. Pure mechanics
(heartbeats, ticks, raw requests) default to no-publish unless a consumer
demands them. Derived events (flap, thresholds) are listed so the raw base
provably supports them, but are stage-4+ consumers, not emitters.
