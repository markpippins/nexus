"""Expression E6 — live Resolution receipt persistence adapter.

This module turns one E5 artifact's canonical receipts into real rows in
``resolution.receipt`` using the exact R4/Q3 contract that the Lilac C3
adapter already uses (V139 DDL):

- idempotency key: ``(source_system, source_receipt_id)`` with
  ``payload_fingerprint`` equivalence (R4);
- kind-scoped producer grants enforced by the DB trigger
  ``resolution.enforce_producer_grant`` (Q3);
- append-only admission receipts are never touched by Expression (kinds are
  limited to the expression evaluation vocabulary);
- explicit outcome classes: accepted, duplicate-equivalent, conflict,
  refused.

The adapter is deliberately small and takes an injectable connection
factory so hermetic tests can point it at a throwaway schema. The DB is the
authority: no code-side copy of the producer registry exists here (F4
doctrine).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from .e5 import E5_REVISION

EXPRESSION_SOURCE_SYSTEM = "expression"
EXPRESSION_PRODUCER_ID = "expression-pipeline"
EXPRESSION_KIND = "expression_evaluation"
CONTRACT_VERSION = 1


class ExpressionPersistenceError(Exception):
    """Canonical persistence refused or conflicted (fail closed)."""


def receipt_source_id(artifact: dict[str, Any], receipt: dict[str, Any]) -> str:
    """Deterministic R4 source id for one Expression evaluation receipt."""
    material = (
        f"{EXPRESSION_SOURCE_SYSTEM}\n{artifact.get('source_run_id')}\n"
        f"{receipt.get('target_id')}\n{receipt.get('evaluation_fingerprint')}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]


def receipt_payload(artifact: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Compact canonical payload for the Expression receipt."""
    return {
        "e5_revision": artifact.get("e5_revision"),
        "source_run_id": artifact.get("source_run_id"),
        "source_fingerprint": artifact.get("source_fingerprint"),
        "proposition_id": receipt.get("target_id"),
        "disposition": receipt.get("disposition"),
        "reason_code": receipt.get("reason_code"),
        "evaluation_fingerprint": receipt.get("evaluation_fingerprint"),
        "request_fingerprint": receipt.get("request_fingerprint"),
        "authority_status": receipt.get("authority_status"),
        "producer_id": EXPRESSION_PRODUCER_ID,
    }


def receipt_refs(artifact: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Stable GIN-indexable references for lineage joins."""
    return {
        "source_run_id": artifact.get("source_run_id"),
        "source_fingerprint": artifact.get("source_fingerprint"),
        "transcript_id": (artifact.get("keychain_context") or {}).get("source_fingerprint"),
        "proposition_id": receipt.get("target_id"),
    }


class ResolutionReceiptWriter:
    """Writes Expression E5 receipts into ``resolution.receipt`` (R4/Q3)."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        schema: str = "resolution",
        producer_id: str = EXPRESSION_PRODUCER_ID,
        source_system: str = EXPRESSION_SOURCE_SYSTEM,
    ):
        self._conn_factory = connection_factory
        self._schema = schema
        self._producer_id = producer_id
        self._source_system = source_system

    def _q(self, sql: str) -> str:
        return sql.replace("%SCHEMA%", self._schema)

    def insert_receipt(
        self,
        conn: Any,
        *,
        kind: str,
        source_receipt_id: str,
        payload: dict[str, Any],
        refs: dict[str, Any] | None = None,
        contract_version: int = CONTRACT_VERSION,
    ) -> tuple[str, str]:
        """Insert one canonical receipt; return (outcome, receipt_id).

        Outcome classes match the Lilac adapter vocabulary:
        accepted, duplicate-equivalent, conflict, refused.
        """
        if kind != EXPRESSION_KIND:
            raise ExpressionPersistenceError(
                f"Expression writes only kind {EXPRESSION_KIND!r}; got {kind!r}"
            )
        fingerprint = _payload_fingerprint(payload)
        cur = conn.cursor()
        try:
            cur.execute(
                self._q(
                    """INSERT INTO %SCHEMA%.receipt
                       (producer_id, kind, source_system, source_receipt_id,
                        payload_fingerprint, payload, refs, contract_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id"""
                ),
                (
                    self._producer_id,
                    kind,
                    self._source_system,
                    source_receipt_id,
                    fingerprint,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    json.dumps(refs or {}, sort_keys=True, separators=(",", ":")),
                    contract_version,
                ),
            )
            receipt_id = cur.fetchone()[0]
            cur.close()
            return "accepted", str(receipt_id)
        except Exception as exc:  # psycopg2 errors surface as explicit outcomes
            conn.rollback()
            message = str(exc)
            if "uq_resolution_receipt_idem" in message:
                stored = self._stored_fingerprint(conn, source_receipt_id)
                if stored == fingerprint:
                    cur.close()
                    return "duplicate-equivalent", source_receipt_id
                cur.close()
                raise ExpressionPersistenceError(
                    f"conflict: source_receipt_id={source_receipt_id} "
                    f"incoming_fingerprint={fingerprint} stored_fingerprint={stored}"
                ) from exc
            if "producer grant refused" in message or exc.__class__.__name__ in {
                "OperationalError",
                "InternalError_",
            }:
                cur.close()
                raise ExpressionPersistenceError(f"refused: {message}") from exc
            cur.close()
            raise ExpressionPersistenceError(f"refused: {message}") from exc

    def _stored_fingerprint(self, conn: Any, source_receipt_id: str) -> str | None:
        cur = conn.cursor()
        cur.execute(
            self._q(
                "SELECT payload_fingerprint FROM %SCHEMA%.receipt "
                "WHERE source_system=%s AND source_receipt_id=%s"
            ),
            (self._source_system, source_receipt_id),
        )
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None

    def record_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        """Persist every canonical receipt from one E5 artifact.

        The caller owns transaction semantics: pass a connection factory
        whose connection is already in a transaction, or let autocommit
        apply per receipt. Returns a deterministic outcome report.
        """
        report: dict[str, Any] = {
            "accepted": [],
            "duplicate-equivalent": [],
            "conflict": [],
            "refused": [],
        }
        with self._conn_factory() as conn:
            for receipt in artifact.get("canonical_receipts", []):
                source_id = receipt_source_id(artifact, receipt)
                payload = receipt_payload(artifact, receipt)
                refs = receipt_refs(artifact, receipt)
                try:
                    outcome, receipt_id = self.insert_receipt(
                        conn,
                        kind=EXPRESSION_KIND,
                        source_receipt_id=source_id,
                        payload=payload,
                        refs=refs,
                    )
                except ExpressionPersistenceError as exc:
                    outcome = str(exc).split(":", 1)[0]
                    report.setdefault(outcome, []).append(
                        {"source_receipt_id": source_id, "reason": str(exc)}
                    )
                    continue
                report.setdefault(outcome, []).append(
                    {"source_receipt_id": source_id, "receipt_id": receipt_id}
                )
        report["artifact_fingerprint"] = artifact.get("artifact_fingerprint")
        return report


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
