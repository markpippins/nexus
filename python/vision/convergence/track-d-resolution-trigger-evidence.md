# Track D — Vision trigger evidence into Resolution (plan 8261645)

**Track:** D — Resolution / Keychains / KG projection
**Package:** D4 — vision trigger-evidence registration
**Owner:** builder · **State:** registered as evidence source only.
**No emitter code changed. No row written. No authority granted.**

> Continuity, not a wave. The vision LOSM satellite already emits governed
> triggers into the shared Keychains outbox. This document registers that
> existing surface so the Resolution track can audit it in parallel; it does not
> modify it.

---

## 1. What vision actually emits (static read, 2026-11-10)

Source: `python/vision/losm-store/src/losm_store/governed_triggers.py`.
Emitter writes into `resolution.keychain_event_outbox` (the append-only shared
event table defined in
`schemas/migrations/resolution/resolution_keychain_event_v1.sql`), with
`ON CONFLICT (source_namespace, source_event_id) DO NOTHING` — i.e. **idempotent
by construction**.

| Factory | `event_kind` | `outcome` | `read_set` keys | `payload` keys |
|---|---|---|---|---|
| `lifecycle_committed` | `vision.lifecycle.transition.committed` | `committed` | `work_request_id`, `from_state`, `to_state` | `reason`, `transition_event_id` |
| `lifecycle_refused` | `vision.lifecycle.transition.refused` | `refused` | `work_request_id`, `from_state`, `requested_state` | `reason` |
| `governance_decision` | `vision.<decision_class>.decision` | caller-supplied | caller-supplied | caller-supplied |
| `receipt_outcome` | `vision.receipt.<event_type>` | caller-supplied | `work_request_id`, `governance_event_id` | caller-supplied |

`governance_decision` normalises `decision_class` by lowercasing and replacing
`_` with `.`; the owning caller supplies the authoritative event id. It is an
adapter over admission / authorization / rollback / knowledge decisions — the
decision authority stays with the owning service, never with vision.

## 2. What this means for the Resolution track

1. **Vision is a satellite.** A vision trigger is a *reference* to a decision
   made elsewhere. Keychains may snapshot it; nothing may read the Mongo
   projection back as operational authority
   (`moleculer/nexus-broker/services/keychain-snapshot.service.ts` header).
2. **Divergence is real and already recorded.** The canonical resolution seed
   contains `representation_disagreement` observations comparing
   `vision.work_requests (LOSM satellite)` against the `work_request` table — for
   example rows `4f71159a-cb9a-4e28-a443-10951a29fb4f`,
   `6bfa19aa-b981-4abf-9168-07f17815e77b` and
   `953ebd7a-f36e-468b-82a2-0a66fc4bf327` in
   `schemas/migrations/resolution/resolution_data_consolidated.sql` show
   `from_repr=work_request, from_value=APPROVED` against
   `to_repr=vision.work_requests, to_value=pending` (and one row where the two
   agree). Those rows are **evidence**, not a defect in this decomposition — they
   are exactly why the satellite must stay non-authoritative until a child plan
   reconciles the representations.
3. **Temporal semantics are the join key.** Both representations are only
   comparable as-of an instant (`as_of`, `effective_at`, `recorded_at` in the
   trigger contract). Comparing them without an instant is meaningless.

## 3. Trigger-class bindings this track requires (links to Track C/D1)

| Vocabulary | Trigger class | Owning authority | Vision role |
|---|---|---|---|
| `transition.vision.decision` | `vision.*.decision` | the caller that supplies the event id | adapter |
| `transition.vision.decision` (lifecycle) | `vision.lifecycle.transition.*` | Conduit WR state machine | emitter |
| `transition.plan.receipt` (as evidence) | `vision.receipt.*` | the receipt ingestor | emitter |

The full class table lives next to the consumer in
`moleculer/nexus-broker/services/keychain-snapshot.service.ts`
(binding block added by D1).

## 4. Evidence slots

| Slot | Value |
|---|---|
| `observed` | static read of `python/vision/losm-store/src/losm_store/governed_triggers.py` (factories at lines 140–240; outbox INSERT at ~121) and of the seeded `representation_disagreement` rows cited in §2 |
| `artifact` | `python/vision/convergence/track-d-resolution-trigger-evidence.md` |
| `timestamp` | 2026-11-10 |
| `verification_owner` | reviewer |
| `unfilled` | a live outbox read (requires a running Postgres with the `resolution` schema and the emitter exercised) — not attempted here |

## 5. Stop condition

Stop after registering the emitter as an evidence source. Reconciling
`vision.work_requests` with `resolution.work_request` is **not** this package:
it needs its own bounded plan, its own as-of instant, and the Resolution owner's
classification (see `schemas/convergence/resolution-jsonb-classification.v1.json`).

Related: `schemas/convergence/README.md` §2,
`schemas/convergence/work-packages.v1.json` (D4).
