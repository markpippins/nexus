"""Git-verifier → PEB admission producer (ruling 6677c394 R2).

The join key between ``execution.*`` and the resolution admission surfaces
does not exist because **no producer has ever written it**. This module is
that producer, per the ruling's producer contract:

- verify a git-ref-commit claim with the existing read-only
  ``GitVerificationAdapter`` (``nexus_core.wrp.git_verifier``),
- persist the claim, the execution-context evidence, and the confirmed
  ``supports`` link into ``resolution.*`` (idempotent via the schema's
  active-key / content-dedup unique indexes),
- submit the PEB admission transaction to the live kernel
  (``POST /api/v1/peb/transaction``, ``toolName='peb_admit_git_execution_claim'``)
  whose ``execution_claim`` envelope triggers ``admit_verified_execution_claim``,
- leave behind a real ``resolution.execution_admission_receipt`` row with the
  real ``execution.attempts.id`` UUID as ``attempt_id`` and the real minted
  ``peb_transaction_id`` (R6 gate).

The ruling's R6 acceptance gate is queryable:

.. code-block:: sql

    SELECT ar.id, ar.attempt_id, ar.peb_transaction_id
    FROM resolution.execution_admission_receipt ar
    JOIN execution.attempts a ON a.id::text = ar.attempt_id
    WHERE ar.admitted IS NOT NULL
      AND ar.peb_transaction_id <> '00000000-0000-0000-0000-000000000001';

Trigger discipline: this module NEVER decides on its own that a run is
admissible — the git adapter verifies, the confirmed link carries the
verification, and ``resolution.admit_verified_execution_claim`` fail-closes.
A rejected/unavailable verification still produces an admission receipt row
with ``admitted=false`` (the gate's explicit design), which is honest
telemetry, not a fabricated correlation.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

from nexus_core.wrp.git_verifier import (
    EVIDENCE_KIND,
    GitVerificationAdapter,
    GitVerificationEvidence,
    GitVerificationRequest,
)

ADAPTER_VERSION = "git-claim-producer/1.0"
PEB_TOOL_NAME = "peb_admit_git_execution_claim"
SOURCE_SYSTEM = "git-verifier"
LINKED_BY = ADAPTER_VERSION


class PEBProducerError(RuntimeError):
    """Raised when the producer cannot complete an admission submission."""


@dataclass(frozen=True)
class GrantContext:
    """Execution context carried from the attempt's work order (grant)."""

    grant_id: str
    lease_id: str
    policy_version_hash: str
    repository_root: str
    base_ref: str
    claimed_ref: str
    claimed_commit: str
    declared_paths: tuple[str, ...]


@dataclass(frozen=True)
class AdmissionSubmission:
    """Outcome of one full producer cycle for one attempt."""

    attempt_id: str
    claim_id: str
    evidence_id: str
    peb_transaction_id: str | None
    admitted: bool
    reason: str
    outcome: str  # verifier outcome: verified | rejected | unavailable


class PGConnection(Protocol):
    """Minimal psycopg2 connection surface used by the persistence step."""

    def cursor(self): ...

    def commit(self) -> None: ...


def _claim_key(attempt_id: str, claimed_commit: str) -> str:
    """Deterministic, unique-while-active claim key (schema unique index)."""
    return f"git-exec:{attempt_id}:{claimed_commit[:12]}"


def _evidence_key(attempt_id: str, evidence_hash: str) -> str:
    return f"git-exec-evidence:{attempt_id}:{evidence_hash[:12]}"


def _policy_hash(context: GrantContext) -> str:
    """Stable policy_version_hash — sha256 over the grant's binding fields."""
    canon = json.dumps(
        {
            "grant_id": context.grant_id,
            "base_ref": context.base_ref,
            "declared_paths": list(context.declared_paths),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()


def verify_attempt(
    attempt_id: str, context: GrantContext, adapter: GitVerificationAdapter | None = None
) -> GitVerificationEvidence:
    """Run the read-only git verification for one attempt's claim."""
    request = GitVerificationRequest(
        grant_id=context.grant_id,
        lease_id=context.lease_id,
        attempt_id=attempt_id,
        # The verifier's _validate_request requires an absolute Path instance
        # (isinstance check), not a string.
        repository_root=Path(context.repository_root),
        base_ref=context.base_ref,
        claimed_ref=context.claimed_ref,
        claimed_commit=context.claimed_commit,
        declared_paths=tuple(context.declared_paths),
        claimant_id=ADAPTER_VERSION,
        policy_hash=_policy_hash(context),
    )
    return (adapter or GitVerificationAdapter()).verify(request)


def persist_claim_and_evidence(
    conn: PGConnection, evidence: GitVerificationEvidence
) -> tuple[str, str, Any]:
    """Insert (or idempotently reuse) the claim, evidence, and confirmed link.

    Idempotency rides the schema's own unique indexes:
      - ``idx_execution_claim_active_key`` on ``claim_key``
      - ``idx_execution_evidence_content`` on ``(source_system, evidence_kind, source_hash)``
      - ``idx_execution_claim_evidence_active`` on ``(claim_id, evidence_id, role)``
    Returns ``(claim_id, evidence_id)`` as text UUIDs.
    """
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            INSERT INTO resolution.execution_claim
                (claim_key, subject_kind, subject_ref, predicate, object_value,
                 policy_version_hash, lease_id, grant_id, attempt_id,
                 declared_by, observed_at, disposition, verification_method,
                 verified_by, verified_at, verification_summary)
            VALUES (%s, 'execution', %s::jsonb, 'executes_commit', %s::jsonb,
                 %s, %s, %s, %s,
                 %s, %s, 'Asserted', 'git_ref_commit',
                 %s, %s, %s::jsonb)
            ON CONFLICT (claim_key) WHERE recorded_until_dt = 'infinity' AND valid_until = 'infinity'
            DO UPDATE SET claim_key = EXCLUDED.claim_key
            RETURNING id
            """,
            (
                _claim_key(evidence.attempt_id, evidence.resolved_commit or evidence.claimed_ref),
                json.dumps({"attempt_id": evidence.attempt_id}),
                json.dumps(
                    {
                        "claimed_ref": evidence.claimed_ref,
                        "resolved_commit": evidence.resolved_commit,
                        "outcome": evidence.outcome,
                    }
                ),
                evidence.policy_version_hash,
                evidence.lease_id,
                evidence.grant_id,
                evidence.attempt_id,
                LINKED_BY,
                evidence.observed_at,
                LINKED_BY,
                evidence.observed_at,
                json.dumps({"outcome": evidence.outcome, "reason": evidence.reason}),
            ),
        )
        claim_id = str(cur.fetchone()[0])

        cur.execute(
            """
            INSERT INTO resolution.execution_evidence
                (evidence_key, evidence_kind, source_system, source_ref, source_hash,
                 captured_at, captured_by, context_kind, policy_version_hash,
                 lease_id, grant_id, attempt_id, verifier_id, verifier_independence,
                 verifier_method, payload)
            VALUES (%s, %s, %s, %s::jsonb, %s,
                 %s, %s, 'execution', %s,
                 %s, %s, %s, %s, %s,
                 %s, %s::jsonb)
            ON CONFLICT (source_system, evidence_kind, source_hash) DO NOTHING
            RETURNING id
            """,
            (
                _evidence_key(evidence.attempt_id, evidence.evidence_hash),
                EVIDENCE_KIND,
                SOURCE_SYSTEM,
                json.dumps({"attempt_id": evidence.attempt_id, "claimed_ref": evidence.claimed_ref}),
                evidence.evidence_hash,
                evidence.observed_at,
                LINKED_BY,
                evidence.policy_version_hash,
                evidence.lease_id,
                evidence.grant_id,
                evidence.attempt_id,
                evidence.verifier_id,
                evidence.verifier_independence,
                evidence.adapter_version,
                json.dumps(
                    {
                        "outcome": evidence.outcome,
                        "reason": evidence.reason,
                        "claimed_ref": evidence.claimed_ref,
                        "resolved_commit": evidence.resolved_commit,
                        "scope_matches": evidence.scope_matches,
                        "undeclared_paths": list(evidence.undeclared_paths),
                    }
                ),
            ),
        )
        row = cur.fetchone()
        if row is None:
            # Content-dedup hit: reuse the existing evidence row id.
            cur.execute(
                """
                SELECT id FROM resolution.execution_evidence
                WHERE source_system = %s AND evidence_kind = %s AND source_hash = %s
                  AND recorded_until_dt = 'infinity' AND valid_until = 'infinity'
                ORDER BY created_at DESC LIMIT 1
                """,
                (SOURCE_SYSTEM, EVIDENCE_KIND, evidence.evidence_hash),
            )
            row = cur.fetchone()
        evidence_id = str(row[0])

        cur.execute(
            """
            INSERT INTO resolution.execution_claim_evidence
                (claim_id, evidence_id, role, verification_state, strength, linked_by, notes)
            VALUES (%s, %s, 'supports', 'confirmed', %s, %s, %s)
            ON CONFLICT (claim_id, evidence_id, role) WHERE expired_at IS NULL
            DO UPDATE SET verification_state = 'confirmed'
            """,
            (
                claim_id,
                evidence_id,
                1.0 if evidence.outcome == "verified" else 0.0,
                LINKED_BY,
                f"verifier outcome: {evidence.outcome}" + (f" ({evidence.reason})" if evidence.reason else ""),
            ),
        )

        # The effective evidence row's capture time (the canonical row when a
        # content-dedup hit reused an existing one). The admission envelope
        # must carry THIS value: the idempotency key is per-attempt, so a
        # replayed cycle has to produce byte-identical transaction input, and
        # a fresh adapter clock would differ every run.
        cur.execute(
            "SELECT captured_at FROM resolution.execution_evidence WHERE id = %s",
            (evidence_id,),
        )
        captured_at = cur.fetchone()[0]
    conn.commit()
    return claim_id, evidence_id, captured_at


def submit_admission(
    evidence: GitVerificationEvidence,
    claim_id: str,
    evidence_id: str,
    captured_at: Any,
    *,
    peb_base_url: str | None = None,
    timeout_s: float = 10.0,
) -> AdmissionSubmission:
    """Submit the admission transaction to the live PEB kernel."""
    base = (peb_base_url or os.getenv("PEB_BASE_URL", "http://localhost:8098")).rstrip("/")
    envelope = evidence.to_peb_admission_envelope(claim_id, evidence_id)
    # Deterministic replay bytes: observed_at is the PERSISTED evidence
    # capture time (see persist_claim_and_evidence), not the adapter's fresh
    # verification clock. Replays under the same per-attempt idempotency key
    # then carry identical input and hit the kernel's recorded-outcome branch
    # instead of being misread as conflicting replays.
    envelope["execution_evidence"]["observed_at"] = captured_at.isoformat()
    payload = {
        "idempotencyKey": f"execution:attempt:{evidence.attempt_id}",
        "entityId": evidence.attempt_id,
        "toolName": PEB_TOOL_NAME,
        "input": envelope,
    }
    req = urlrequest.Request(
        f"{base}/api/v1/peb/transaction",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout_s) as resp:
            body: Mapping[str, Any] = json.load(resp)
    except urlerror.HTTPError as exc:
        # 422 = admission rejected (the kernel's explicit fail-closed answer).
        # The receipt row exists with admitted=false; surface the reason.
        try:
            body = json.load(exc)
        except Exception:
            body = {"message": f"HTTP {exc.code}"}
    except (urlerror.URLError, TimeoutError) as exc:
        raise PEBProducerError(f"PEB kernel unreachable at {base}: {exc}") from exc

    admitted = bool(body.get("admitted"))
    reason = str(body.get("reason") or body.get("message") or "")
    tx_id = (
        body.get("transaction_id")
        or body.get("transactionId")
        or body.get("peb_transaction_id")
        or body.get("id")
    )
    return AdmissionSubmission(
        attempt_id=evidence.attempt_id,
        claim_id=claim_id,
        evidence_id=evidence_id,
        peb_transaction_id=str(tx_id) if tx_id else None,
        admitted=admitted,
        reason=reason,
        outcome=evidence.outcome,
    )


def produce(
    attempt_id: str,
    context: GrantContext,
    conn: PGConnection,
    *,
    adapter: GitVerificationAdapter | None = None,
    peb_base_url: str | None = None,
) -> AdmissionSubmission:
    """One full producer cycle: verify → persist claim/evidence/link → admit."""
    verified = verify_attempt(attempt_id, context, adapter)
    claim_id, evidence_id, captured_at = persist_claim_and_evidence(conn, verified)
    return submit_admission(
        verified, claim_id, evidence_id, captured_at, peb_base_url=peb_base_url
    )
