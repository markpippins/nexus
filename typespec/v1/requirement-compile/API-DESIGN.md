# Requirement Compile-to-Plan Capability API

**Status:** Implemented in Nebula v1 capability routes; database migration 072 must be applied before use.
**Capability:** `nexus.requirement.compile-to-plan/1.0`
**TypeSpec source:** `main.tsp`, `models.tsp`, and `operations.tsp` in this directory.

## 1. Purpose and boundary

Expose one named semantic capability over the existing Nebula requirement compiler:

> Read a requirement and its current context, produce an explainable compilation into WorkRequest-oriented IR, and optionally request creation of a pending implementation plan.

Compilation is a proposal-producing computation. Even when the plan is created, its `pending` state records neither acceptance nor execution. This capability never authorizes opcode execution. Downstream admission, plan-state transitions, execution, and verification remain separately governed operations with their own authority and evidence.

This is deliberately an API over the capability, not a promise that registry opcodes are currently interpreted by a general-purpose execution engine.

## 2. HTTP surface

Base path: `/api/v1/capabilities/requirement-compile`.

### `POST /compile`

Request:

```json
{
  "requirementId": "existing Nebula requirement ID",
  "createPlan": false,
  "dryRun": false,
  "deliverable": "optional description",
  "idempotencyKey": "optional caller key"
}
```

Outcomes:

- `200 compiled`: IR generated, no plan created (includes dry run; `planRequested` still reflects a suppressed request).
- `201 plan_created`: IR generated and pending plan creation confirmed.
- `200 partial`: compilation succeeded but a requested plan-creation side effect did not complete. The result retains IR and names the failed effect. Receipt persistence failure returns `500` with `RECEIPT_PERSISTENCE_FAILED` because no durable receipt exists to prove a partial outcome.
- `400` malformed input; `404` requirement or receipt not found; `409` idempotency-key conflict or request in progress; `422` reserved for invalid/unsupported compilation input; `500` unexpected or receipt persistence failure.

The TypeSpec declares HTTP outcomes using response-wrapper models (status-code property plus body), which are necessary to emit these codes in OpenAPI. `200` includes dry-run and partial payloads whose `outcome` differentiates them; `201` is reserved for confirmed pending-plan creation. Existing callers retain `POST /api/requirements/:id/compile`; the versioned adapter is additive.

### `GET /receipts/{receiptId}`

Return the immutable compilation receipt so callers can inspect the recorded input, selected mapping, IR, effects, and evidence. A future list/query endpoint may be added only when access controls and retention semantics are defined. Failed compilation receipts are available by the `receiptId` in the error body.

## 3. Result and receipt

The receipt schema marker is `nexus.compilation-receipt/v1`. The TypeSpec models define the camelCase wire form returned by the versioned adapter:

- `CompilationResult`: explicit outcome, requirement snapshot, registry selection or fallback, IR, warnings, receipt ID, and effect state.
- `CompilationReceipt`: versioned immutable audit fact for one attempt, including normalized source, compiler identity/version, selected registry version and method, IR, optional digests, requested/completed plan effect, linked journal/evidence IDs, warnings, and failure detail.

Persist the full canonicalized input and IR when replay/explanation is required; a digest alone cannot reconstruct them. If stable source revision or compiler build identity is unavailable, record null and set `replayable=false` rather than claiming deterministic replay.

`CompilationEffect.executionAuthorized` is the literal `false`; there is intentionally no execution operation in this capability.

## 4. Selection semantics

A registry match must identify the entry ID, intent ID/version, selection method, and matched pattern (or a stable representation of the selector). Fallback is not equivalent to a registry match and must be labeled with a reason. Under the current compiler, selection is longest regex-substring matching over title plus intent summary; it is a candidate-selection mechanism, not proof that the operation's semantic contract satisfies the requirement.

Before treating a mapping as applicable, a later implementation should validate required parameters, declared effect class, and typed input/output contracts. Preconditions and postconditions should not be described as enforced until an evaluator actually checks them and records evidence.

## 5. Effect and retry semantics

- `dryRun=true` guarantees no plan write and no execution-side effect. It suppresses `createPlan` if both flags are true. An append-only receipt is persisted and marked `dryRun=true`.
- `createPlan=true` invokes only the plan-creation boundary and reports the returned plan number and observed status. The current `POST /api/plans` inserts a plan with `pending` status.
- The idempotency key applies to plan creation as well as compilation. Plan creation requires a caller key. Repeating the same key with identical canonical input returns the original outcome/receipt; reusing it with different input fails `409`.
- Compilation and plan creation are not one transaction. Plan creation failure preserves the compiled IR and a `partial` receipt. Receipt persistence failure is an explicit `500`; the response carries the attempt receipt ID and advises retrying only with the original idempotency key.
- Receipt persistence is append-only. Corrections or retries create new linked receipts, not updates to earlier receipts.

## 6. Evidence and provenance

Minimum receipt lineage:

1. requirement ID and source revision/fingerprint when available;
2. normalized requirement snapshot and cross-reference IDs;
3. registry entry ID/version and selection method;
4. compiler/service build identity when available;
5. emitted IR (and digest if canonicalization is defined);
6. journal record ID and pending plan number/status if created;
7. explicit warning/error/effect outcome.

A generated plan is evidence that a plan row was created, not evidence that the proposed instructions ran or achieved their postconditions. Execution evidence must come from the downstream execution/receipt path.

## 7. Mapping to current implementation

| Capability contract | Implementation |
| --- | --- |
| Requirement and hierarchy read | `POST /api/requirements/:id/compile` Stage 1 |
| Cross-reference/criteria normalization | Same route Stage 1 |
| Registry matching and opcode-template expansion | Same route Stage 2 |
| Dry-run result | Versioned adapter suppresses plan creation, returns IR, and persists a receipt |
| Optional pending plan creation | Same route calls `POST /api/plans` when `createPlan=true` |
| Plan row | `/api/plans` inserts into blueprint history and returns plan number/status |
| Thin compilation journal | Same route attempts an `agent_records` engineering log; receipt links its ID when returned |

Known limitations: required parameters are selected but not validated; preconditions/postconditions are not evaluated; template substitution is not implemented; longest-regex matching is syntactic rather than semantic validation. The receipt records replayable=false because stable compiler/source revision identity is unavailable. Plan-service responses without a confirmed plan number are surfaced as partial outcomes. Apply migration 072 before enabling the versioned route.

## 8. Verification and remaining work

HTTP conformance tests cover request validation, required plan idempotency keys, dry-run and no-plan behavior, receipt retrieval, stable retries, changed-input conflicts, confirmed pending-plan creation, explicit partial outcomes, persistence failures, missing receipts, and the permanent false execution-authority boundary. `conformance.json` lists eight provider-independent black-box scenarios. The migration creates durable idempotency reservations and append-only receipts. The migration is content-attested and remains unapplied by this implementation task; deploy only through the DBA-approved migration workflow. Required-parameter and typed opcode argument validation remain future work.

## 9. Non-goals

- No new opcode vocabulary or interpreter.
- No plan approval, ticket lifecycle, deployment, filesystem mutation, or shell-command execution.
- No assertion that current registry metadata constitutes authorization.
- No claim that source inspection establishes which migrations are live-applied.
