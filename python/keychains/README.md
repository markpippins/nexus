# keychains (Python)

Python port of `moleculer/nexus-broker/services/keychain-snapshot.service.ts` —
**contextual-core slice**.

The keychains store is a one-way `PG -> Mongo` projection. Nothing in it is
operational authority: the canonical substrate is the resolution schema
(`authority-matrix.json` domain `sol_ir`), and Mongo is never read back as
operational state (D-2026-08-14-003). Two projection domains exist in the
TypeScript service; this slice ports the second one:

> **Agent records (keychain contextual layer).** Projects
> `nebula.agent_records` into keychain entries — one document per *logical
> record instance* holding only the **current (latest) issuance**. Historical
> issuances are not duplicated: a supersession chain is resolved to
> `R -> v5`, not `v1..v5`. Provenance (issuer, time, supersedes) is preserved.
> This is the contextual instance layer: "what the next turn knows."

## What is ported

| Moleculer action | Python |
|---|---|
| `agentRecordsSnapshot` | `agent_records_snapshot(...)` |
| `agentRecordsStatus` | `agent_records_status(store)` |
| `agentRecordsTransitions` | `agent_records_transitions(store, ...)` |
| `status` | `status(store)` (event-driven posture only) |
| startup reconciliation | `reconcile_committed_checkpoints(store, ...)` |

Preserved behaviours, in the same order as the service:

- trigger normalisation (`normalize_trigger_event`, `parse_legacy_trigger`,
  `outbox_row_to_trigger`) with `idempotency_key = <namespace>:<source_event_id>`
- doctrine-snapshot validation and one-time persistence (`doctrine_snapshots`)
- binding-decision detection for the ratified `deny_contract_promotion` class,
  and the bounded observation window it carries
- idempotent delivery: dedup by idempotency key, recovery of an already
  committed checkpoint by `(source_namespace, source_event_id)`
- archive-only handling of non-binding negative outcomes (`not_applicable`,
  no state-vector checkpoint)
- sparse reservation with takeover of a stale reservation (5 min TTL)
- supersession resolution: amendment chains (`<token> — amendment v<N>:`),
  explicit `supersedes:<prefix>` chains walked to the tip, everything else
  single-issuance; asset reference attached from the *current* record
- D6 checkpoint storage: every Nth checkpoint is a BASE (`KEYCHAIN_BASE_INTERVAL`,
  default 50), the rest are DELTAs carrying `base_version`; the prior state
  comes from the bounded `checkpoint_heads` pointer, falling back to a legacy
  checkpoint's parallel id arrays, and a delta is **never** computed against
  unknown prior state (no prior index, or <50% key overlap, degrades to a base)
- staged -> committed -> promoted protocol, with the compatibility views
  (`entries`, `record_type_state`) refreshed only while this checkpoint is
  still the active one
- drift findings (`new_instance`, `superseded`) written to `ar_drift_findings`

## Not ported (next slice)

`snapshot` (legacy observations/assessments projection),
`agentRecordsDoctrineSets`, `agentRecordsDoctrineReconstruction`,
`agentRecordsDoctrineBackfill`, `agentRecordsCensus`, `agentRecordsRewind`,
and the `keychain_event_outbox` polling loop that drives triggers. Those need
`lib/doctrine-query.ts` (375 lines) and `lib/census-report.ts` (523 lines)
ported first, plus the rewind replay path over base + deltas.

## Layout

```
python/keychains/
├── models.py            value types (AgentRecord, KeychainEntry, manifest, delta)
├── trigger.py           trigger contract normalisation
├── supersession.py      logical-instance resolution
├── checkpoints.py       base/delta mechanics, index derivation, merge
├── decision_context.py  decision-context manifest + observation window + doctrine
├── store.py             KeychainStore protocol, MemoryKeychainStore, MongoKeychainStore
├── projection.py        project_agent_records — the checkpoint protocol
├── api.py               action-shaped entry points
└── tests/               66 unit tests, no database required
```

The projection talks to `KeychainStore`, a small intent-named interface, rather
than to Mongo query syntax. `MemoryKeychainStore` implements it over dicts (all
tests run against it); `MongoKeychainStore` is the production adapter and takes
an already-connected client, so `pymongo` is imported by the caller, not here.

## Usage

```python
from keychains import AgentRecord, MemoryKeychainStore, agent_records_snapshot

records = [AgentRecord.from_row(row) for row in rows]  # nebula.agent_records
result = agent_records_snapshot(records, store, trigger_event=trigger)
```

Read the prior state from Postgres with:

```sql
SELECT id, record_type, role, title, content, source_path, metadata, tags,
       system_id, subsystem_id, feature_id, plan_ref, candidate_id,
       requirement_id, created_at, level, visibility_scope, model
FROM nebula.agent_records
ORDER BY created_at
```

and the asset map from `semantics.canonical_asset` where
`canonical_asset_id LIKE 'asset:nexus:nebula_agent_records:%'` and
`expired_at IS NULL` (mirrors the TypeScript query).

## Parity divergences

Three places where the port is deliberately *not* byte-identical. Each is a
finding for the TypeScript owner, not a silent rewrite:

1. **Drift comparison uses `instance_id`.** The service compares
   `entries.keys()` — the prefixed map keys `single:<uuid>` /
   `amendment:<token>` / `explicit:<prefix>` — against a prior index built from
   *unprefixed* `entry.instance_id` values (the same index it writes to
   `checkpoint_heads`). Every instance therefore reads as `new_instance` on
   every checkpoint and `superseded` can never fire. This port compares
   `entry.instance_id`, which is the evident intent; a test pins it
   (`test_second_projection_is_a_delta_against_the_head_index`).
2. **The `doctrine snapshot reference mismatch` guard is unreachable.** The id
   is resolved as `snapshot.snapshot_id || trigger.doctrine_snapshot_id || ...`
   and then compared with the snapshot, so an embedded snapshot always supplies
   its own id. The port keeps that ordering (contract-preserving) and its test
   documents the behaviour instead of asserting a guard that cannot fire.
3. **An explicit chain whose tip is also an amendment record disappears
   whole.** Entry 2 skips any explicit chain that contains an amendment member,
   and entry 3 skips chain members, so the older record is represented by
   nothing. Reproduced faithfully, with a test naming it
   (`test_explicit_chain_yields_to_an_amendment_member`).

## Testing

```bash
python3 -m pytest python/keychains -q      # from the repo root
```

No Postgres, Mongo or network access is required.
