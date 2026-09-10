# Wind Phase B: execution-request / receipt seam

Migration: `sql/V151__wind_execution_request_receipt_seam.sql`

This slice proves the evidence seam without making Wind or Aegis an execution
or admission authority.

## Contract

`POST /api/execution-requests` persists an immutable, advisory request pinned
to an artifact type/reference/revision/fingerprint and evaluator/read-set
digests. It also carries causation/correlation IDs, provider and invocation
contracts, a failure policy, and an idempotency key. The server computes and
stores a request digest.

The same idempotency key and identical material is a replay (`200`,
`replay: true`). The same key with different material is a `409` conflict;
there is no overwrite path.

`POST /api/execution-requests/:id/attempts` records one provider outcome. It
never invokes a provider. Outcome classes are exactly `SUCCEEDED`, `FAILED`,
`UNAVAILABLE`, `STALE`, and `INVALID`. Result JSON is content-addressed. A
retry is a new attempt number and may carry `parent_attempt_id`; duplicate
attempt keys replay only identical material.

`POST /api/execution-requests/:id/receipts` creates one immutable advisory
receipt for an attempt. The receipt status and result digest must match the
attempt. A second receipt for the attempt is an identical replay or a `409`
conflict. Evidence references and lineage are retained as JSON, not interpreted
as admission.

## Safety boundaries

- No endpoint calls a provider or harness.
- No endpoint activates a workflow version.
- No endpoint mutates a Wind ticket, instance, Resolution row, or PEB row.
- Database triggers make requests, attempts, and receipts append-only.
- `authority_level` is database-constrained to `advisory`.
- A truthful failure/unavailable/stale/invalid outcome remains evidence; it is
  never normalized to success or verified.

## Advisory provider invocation envelope

The request's provider fields are validated by the pure
`validateProviderInvocation()` contract validator before persistence. Version 1
requires provider and adapter identity, input/output schema digests, an
explicit invocation mode, a bounded timeout, cancellation mode, retry budget,
evidence-reference policy, and explicit failure mappings. Failure mappings may
never turn an unavailable, timeout, malformed, or stale result into
`SUCCEEDED`.

Validation performs no provider or harness I/O. It only normalizes the plan;
actual adapter dispatch remains a future slice. Authority fields are
fail-closed: only `advisory` is accepted and lifecycle admission/mutation
fields are refused.

The next slice can add a bounded provider adapter only after the architect
ratifies this invocation contract and the admission handoff. Resolution/PEB
remains the only admission boundary.
