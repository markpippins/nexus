# Downstream Projection Conformance — witnessed-run (W5.08 engineer side)

**Status:** Engineer-side evidence for W5.08 (co-owned with design-synthesist).
Cites only merged, verified artifacts. Scope: the consumer contract for
downstream consumers (including §10 core and governed UIs) of the W3.08
governed projection.

## The projection surface

- Route: `GET /api/execution/projections/witnessed-runs?workflow_instance_id=&node_id=`
- Implementation: `typescript/execution-srv/src/routes.ts` (W3.08, merged PR #95)
- Version: `WITNESSED_RUN_PROJECTION_VERSION = 2` — bumped only on breaking
  shape changes. v2 (ruling 6677c394 R3): receipt slots re-pointed to live
  sources — `peb_admission` = PEB admission receipt
  (`resolution.execution_admission_receipt`, written by the git-claim
  producer), `conduit_transition` = native `EXECUTION_COMPLETE` receipts
  (`execution.receipts`). v1's slots were structurally unsatisfiable
  (always NULL; tombstone under ruling e62992f0 R1)
- Read-only: SELECTs only, no write path
- Identity correlation only: governance payloads are never included

## Consumer contract (C1–C5)

Verified at runtime by `typescript/§10 core/scripts/run-w508-conformance.ts`
(evidence in `docs/w508-evidence/w508-conformance.json`):

### C1 — Versioned contract
Consumers pin to `projectionVersion` and must fail closed on a mismatched
version. The projection payload carries `projectionVersion`, `projection`
name, `generatedAt`, and `sourceUpdatedAt` for staleness checks.

### C2 — Server-derived only (AC4)
The authoritative status (`complete`, `missing_lineage`, `unknown`, `stale`,
`refusal`, `drift`, `duplicate_retry`) is computed SERVER-side by
`classifyWitnessedRunStatus`. Consumers display or branch on the result —
they never re-derive the witnessed-run join locally. Verified: the §10 core
consumer (`witnessedRun.ts` `normalizeProjection`) consumes the server status
verbatim for all 7 states; local classification is only a fallback for
unclassified payloads.

### C3 — Read-only consumer surface
The consumer adapter (`ReadOnlyWitnessedRunAdapter`) exposes `get` only — no
write path a UI could use to push state back into execution-srv. A missing
source (404) yields an empty `missing_lineage` projection with null
identities (no fabrication); server errors propagate as errors (no
synthesized status).

### C4 — Identity correlation
The governed adapter (`adapter/governed.ts`) validates the projection
manifest on every response: `schemaVersion === 1`, `source === "server"`,
valid `sha256:` artifact digest, integer version. Any mismatch fails closed
with `PROJECTION_IDENTITY_MISMATCH`; non-server sources are rejected with
`SOURCE_NOT_GOVERNED`. Governance payloads are never smuggled through the
projection.

### C5 — No UI admission path
The §10 core runtime exposes no API that lets a UI/browser submit admissions
or flip blocking authority. Admission is only reachable via
`ContractAdmissionRegistry.admit` (W4.06, PR #99), which the governed adapter
does not export. The registry remains fail-closed for incomplete sets.

## Server-derived fields (per record)

| Field | Meaning |
|---|---|
| `status` | authoritative join state (server-classified) |
| `missingLineage` | enumerated missing elements: `envelope_id`, `evaluation_fingerprint`, `manifest_id`, `peb_admission_receipt`, `conduit_transition_receipt`, `evidence_ids` |
| `identities` | envelope/fingerprint/manifest/receipt/evidence correlation ids |
| `assessment` | disposition + status (server-held, echoed) |
| `replay` | fixture id + status |
| `projectionVersion` | contract version consumers pin to |

## Consumer checklist (for UI implementers)

1. Pin `projectionVersion` and fail closed on mismatch.
2. Render `status` verbatim; never re-derive from raw metadata.
3. Render `missingLineage` as diagnostics; do not attempt repair client-side.
4. Treat `unknown` as indeterminate — surface to the operator, never guess.
5. Never attempt admission from the UI; admission is boundary-only (C5).
6. Handle 404 as an explicit empty `missing_lineage` state, not an error.
7. Handle 5xx as errors; never synthesize a status from a failed request.

## Evidence baseline

- W3.08 projection (PR #95) + witnessed-run conformance (11 suites green).
- W5.03 admission-boundary verification (PR #102, fingerprint `sha256:178e269c…`).
- W5.04 canary (PR #103, fingerprint `sha256:dc0fe075…`).
- W5.05 drill operations (PR #104, fingerprint in `docs/w505-evidence/`).
- Full §10 core conformance suites exit 0; pytest 101 passed / 30 skipped.
