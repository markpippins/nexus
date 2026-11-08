# Architecture Reports as Keychain-Anchored Documents

> **Status: DRAFT — proposed, not ratified.** Author: architect. This proposes a
> storage shape for architect reports; it does not change where reports are
> canonically stored until the operator rules on the open questions at the end.
>
> Sponsor observation that triggered it: *"architecture-reports sounds like
> something that belongs in mongodb that should be indexed with keychains."*

## 1. What exists today

| Surface | State |
|---|---|
| `nebula.agent_records` | canonical, append-only; `record_type='report' AND role='architect'` = **2,801 rows, ~1.87 MB** |
| `nebula.projections` entry `architecture-reports` | deterministic; one `.md` file per report into `audit/ARCHITECTURE/reports/` |
| `resolution.keychain_event_outbox` | **336 rows** — the emitted keychain events |
| `resolution.keychain_evaluation_manifest` | 0 rows — evaluation manifest, so far unused |
| SOLScript keychains | `KeychainEvent` contracts with read-set digests — real, in `typescript/solscript` (TS core) and `python/SOLScript` (python core) |
| MongoDB | `atomic-mongodb` (mongo:4.4.18) running and reachable on :27017; driver already a dependency of `typescript/tsp-eav-emitter`, `moleculer/nexus-broker`, `moleculer/search` |

Two facts settle the framing:

1. **This is not a capacity decision.** 2,801 documents of 1.87 MB total fit in a
   single Postgres table without complaint. Nothing is being offloaded for size.
2. **It is a shape decision.** A deterministic projection writing 2,801 markdown
   files produces an archive nobody queries, and it carries no provenance: a file
   in `audit/` cannot say *which event produced it* or *whether it was edited
   after the fact*. Keychains answer exactly that question, so the operator
   instinct is right — but the thing worth moving is the **index**, not the
   canonical text.

## 2. Decision

**Postgres remains canonical for report content. MongoDB becomes the queryable,
keychain-anchored index over it.** Mongo is a projection target in the sense the
database-first doctrine already uses: derived from `nebula.agent_records`, never
the source of truth, never written to directly by agents.

```
nebula.agent_records (canonical, append-only)
        │  ingest (idempotent, upsert by record id)
        ▼
architecture-reports indexer ──► resolution.keychain_event_outbox (anchor event)
        │                                  │
        ▼                                  ▼
architecture_reports (MongoDB)  ←  keychain event id + digest
        │
        ▼
projection renders a MANIFEST (chain head + recent rows), not 2,801 files
```

## 3. Document shape

```jsonc
{
  "_id": "9e25a0c7-e55f-47e3-ab63-313e76ea75d1",   // = agent_records.id (uuid)
  "title": "Systems model expanded: 45 per-app subsystems added…",
  "role": "architect",
  "record_type": "report",
  "created_at": "2026-10-31T19:41:02Z",
  "tags": ["type:status-update", "to:engineer"],
  "level": 3,
  "visibility_scope": "all",
  "content": "<full markdown, untruncated>",
  "content_digest": "sha256:…",                    // over canonical JSON, see §5
  "keychain": {
    "event_id": "<uuid>",                          // resolution.keychain_event_outbox row
    "digest": "sha256:…",                          // keychain event digest
    "read_set_digest": "sha256:…",                 // null when not derivable — see §6
    "chain_position": 1234,
    "prev_digest": "sha256:…"                      // links this doc to its predecessor
  },
  "source": {
    "pg_table": "nebula.agent_records",
    "pg_txid": 17812572,
    "ingested_at": "2026-10-31T19:42:11Z",
    "schema_version": 1
  }
}
```

### Indexes

| Index | Purpose |
|---|---|
| `{_id: 1}` unique | idempotent upsert target; one document per report |
| `{keychain.digest: 1}` unique | the keychain anchor is single-use — a duplicated anchor is a bug, not a retry |
| `{keychain.chain_position: 1}` unique | makes a fork in the chain impossible to store |
| `{created_at: -1}` | "what did the architect decide recently" |
| `{role: 1, created_at: -1}`, `{tags: 1, created_at: -1}` | role/tag routing queries |
| `{title: "text", content: "text"}` | exploratory search only — not authoritative for retrieval, which stays in `nebula` per the knowledge-stratification doctrine |

No TTL index: governance records never expire.

## 4. Two chains, deliberately not the same chain

- **Content chain** — `prev_digest` links consecutive ingested documents for one
  `(role, record_type)` stream. This answers *"was a document inserted, removed or
  reordered after the fact?"*
- **Keychain anchor** — `keychain.digest` ties the document to the SOLScript
  evaluation that produced the decision. This answers *"which evaluation
  justified this?"*

They are stored separately because they can legitimately diverge: a report can be
anchored to an evaluation that happened before other reports were written.

## 5. Canonical serialization (the part that must not be improvised)

A digest is only meaningful if the bytes are deterministic. The indexer computes
`content_digest` over a **canonical JSON** serialization: keys sorted, UTF-8, no
insignificant whitespace, explicit `null`s, timestamps as ISO-8601 UTC with
millisecond precision. Any change to that canonicalization is a
`schema_version` bump and requires a full re-anchor, because every downstream
digest becomes invalid — this is the one migration that cannot be done
incrementally.

`agent_records` is append-only (doctrine I4), so the indexer treats a digest
change for an existing `_id` as **drift, not an update**: it writes a
`quarantine/architecture_reports_conflicts` record and leaves the original
document untouched. A report whose digest changed under the same id means
something bypassed the append-only path.

## 6. The honest gap: reports probably have no read-set

`KeychainEvent` carries a read-set digest because SOLScript evaluations read
declared sets. An architect report is prose — it does not have a formal read-set
today. Three options, in order of preference:

1. **Anchor by evaluation when one exists** (`read_set_digest` populated), and
   `null` otherwise. The chain still gives tamper-evidence; it just does not claim
   a read-set it cannot prove.
2. **Derive a read-set from `source.pg_txid` + touched tables** (observable, low
   effort, weaker claim).
3. **Invent read-sets for prose** — rejected. Fabricating a provenance claim that
   no evaluation made is exactly the failure mode the attestation gates (G1/G2)
   exist to prevent.

This is the main thing the operator should rule on, because it decides how strong
a claim the index is allowed to make.

## 7. Consistency and failure modes

| Failure | Behaviour |
|---|---|
| Indexer down | Mongo lags; a `lag` counter is exposed. Nothing reads Mongo for correctness, so no service degrades. |
| Mongo down | Ingestion queue drains later; the manifest projection reports staleness rather than rendering a stale head as current. |
| Duplicate delivery | Upsert by `_id`; unique `keychain.digest` makes a double-anchor a loud error. |
| Digest mismatch on same `_id` | Quarantine + alert (§5). Never silently overwrite. |
| Chain verification failure | Same quarantine path; a chain break is an integrity incident, not a data error to repair in place. |

## 8. Migration path (order matters)

1. **Upgrade MongoDB off 4.4.18 first.** 4.4 reached end-of-life in Feb 2024;
   building new governance storage on an EOL server, with a driver whose supported
   server matrix excludes it, is the wrong order. → devops handoff.
2. Create `architecture_reports` + indexes (§3) and the quarantine collection.
3. Backfill the 2,801 existing reports (idempotent; re-runnable), building both
   chains as it goes.
4. Stand up the indexer on the `keychain_event_outbox` path and verify the chain
   end-to-end with a synthetic insert → anchor → verify cycle.
5. **Only then** switch `architecture-reports` to render a manifest, and stop
   writing 2,801 files. Keep the file renderer available behind an explicit call
   for one release cycle.

## 9. Open questions for the operator

1. **Read-set honesty** (§6): anchor-only-when-provable (recommended), txid-derived,
   or something else?
2. **Chain granularity**: one global chain, one per `(role, record_type)`, or one
   per thread/work-ref? A per-thread chain is the most useful for review and the
   most work to maintain.
3. **Scope**: architect reports only, or every governance record type
   (`decision`, `assessment`, `inspection`, `attestation`)? The design generalises,
   but each type adds a chain.
   > **Update (2026-10-31):** my answer here is now to generalize. `keychains.record_type_state`
   > already tracks nine record types with `instance_id`, `version`, `role` and
   > `supersession_type`, so the chain exists for all of them and the collection should be
   > one `governance_records` with a type discriminator rather than `architecture_reports`.
   > Proposed citable set: decisions, attestations/assessments, inspections, notes, and only
   > the reports a decision cites; exclude `engineering_log`, `prompt`, `response`. Rationale
   > and evidence: Assembly `discussions` thread `b80e1a9b`.
4. **Authority**: does the Mongo index ever become citable as evidence, or is it
   strictly a convenience replica of `nebula`? This decides how much verification
   ceremony it needs.
