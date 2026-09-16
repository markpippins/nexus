# Governed Projections for Downstream Consumers (W3.08)

**Status:** Implementation (PR open for Architect review). Read-only,
versioned, server-derived projections so downstream consumers never
reconstruct governance locally.

## Principles

1. **Server-derived only.** Status classification, missing-lineage
   enumeration, and receipt correlation are computed by execution-srv.
   Consumers display or branch on the result — they never re-derive the
   witnessed-run join or classify states themselves (extends W2.06 AC4
   from "no browser-owned reconstruction" to "no consumer-owned
   reconstruction").
2. **Versioned contract.** Every response carries `projectionVersion`.
   Bump only on breaking shape changes. Consumers pin the version they
   understand and **fail closed** on mismatch or unrecognized status
   values.
3. **Identity correlation only.** The projection carries identities
   (envelope id, receipt ids, evidence ids) and derived verdicts — never
   governance payloads (no contract digests, no law documents, no
   assessment reasons beyond disposition/status).
4. **Read-only.** SELECTs only; no write path exists in execution-srv.
5. **Dormant authority.** Nothing here activates `peb.decisions` or any
   advisory/blocking path. Wave 2 ceiling (shadow/read-only) holds.

## Endpoint

```text
GET /api/execution/projections/witnessed-runs?workflow_instance_id=&node_id=
```

### Response shape (projectionVersion: 1)

```jsonc
{
  "projectionVersion": 1,
  "projection": "witnessed-run",
  "generatedAt": "2026-08-30T00:00:00.000Z",
  "sourceUpdatedAt": "2026-08-30T00:00:00.000Z",   // request row's updated_at
  "workflow": { "instanceId": "wf-0007", "nodeId": "node-admission" },
  "request": { "id": "<request uuid>" },
  "identities": {
    "envelopeId": "env-1",
    "evaluationFingerprint": "sha256:fp",
    "manifestId": "m-1",
    "pebAdmissionReceiptId": "<uuid>",            // separate id, never merged
    "conduitTransitionReceiptId": "<uuid>",       // separate id, never merged
    "evidenceIds": ["ev-1"]
  },
  "assessment": { "disposition": "allow", "status": "admitted" },
  "replay": { "fixtureId": "F01", "status": "replay_ok" },
  "status": "complete",                            // 7-state vocabulary (W2.06)
  "missingLineage": []                             // enumerated when incomplete
}
```

`missingLineage` values: `envelope_id`, `evaluation_fingerprint`,
`manifest_id`, `peb_admission_receipt`, `conduit_transition_receipt`,
`evidence_ids`.

### Status vocabulary

Same seven states as W2.06 (`complete`, `missing_lineage`, `unknown`,
`stale`, `refusal`, `drift`, `duplicate_retry`), derived server-side by
`classifyWitnessedRunStatus` — including the PR #87 fix (`unknown` when a
row carries no lineage elements at all).

### Error behavior

| Case | Response |
|---|---|
| Missing query params | 400 |
| No matching run | 404 |
| DB error | 500 (message included; internal observability service) |
| Success | 200 with `Cache-Control: no-store` |

## Consumer contract (fail-closed)

Downstream consumers (projection-core client, UIs, future tooling) must:

1. Pin `projectionVersion` — reject the payload on mismatch.
2. Treat any unrecognized `status` as `unknown` **and refuse to act on it**
   (fail closed), logging the unrecognized value.
3. Never re-derive status from the identity fields; the server's `status`
   is authoritative.
4. Correlate receipts by their separate ids — PEB admission and Conduit
   transition identities are distinct and must stay distinct (W2.06 AC3).

## Verification

- `npm run test:witnessed-run` (execution-srv) — covers the projection
  endpoint: version field present, complete join, partial join with
  enumerated missing lineage, empty lineage → `unknown` with all six
  elements enumerated, 404/400 paths, and no payload leakage
  (no `law`/`contractDigest` in responses).
- W2.06 conformance suite unchanged and green.
