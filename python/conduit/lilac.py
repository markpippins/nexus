"""Lilac canonical Resolution persistence adapter (C3, plan 8261639).

Implements the engineer lane of the C2 contract draft (architect record
034eb36c) as amended by binding rulings:

- 1394292c — R2 authority boundary, R4 idempotency contract (``ON CONFLICT
  DO NOTHING`` is fallback, never contract), R5 lineage discipline.
- a515667d (Q3) — ONE canonical stream ``resolution.receipt``, kind-
  discriminated, with kind-scoped producer grants (authority != storage).
- 8d30e540 (Q1) — legacy surfaces become C5 dual-read projections; not
  canonical.

STAGED, NOT ACTIVATED: nothing calls this module in a write capacity until
C2 is formally ratified and C3 cutover is authorized. The only live hook is
the env-gated shadow-write seam in ``db_adapter.py``
(``CONDUIT_LILAC_SHADOW=1``) which records canonical outcomes alongside —
never instead of — the legacy write. Outcomes are advisory evidence for the
C2 ratification record.

Contract surface (R4):
  receipt    idempotency: (source_system, source_receipt_id,
             payload_fingerprint) — conflicting replay → refused +
             both fingerprints recorded.
  ticket     idempotency: (workflow_ref, role, position, generation).
  transition idempotency: (ticket_id, from, to, input_receipt, policy).
  fan-out    idempotency: (input_receipt_id, kind, fan_out_policy_version)
             — the ledger IS the single receipt-to-ticket fan-out.

Outcome classes (observable, distinct): accepted · duplicate-equivalent ·
conflict · refused · unlinked · quarantined.
"""
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("conduit.lilac")

# Contract version of THIS adapter implementation (V139 shapes).
LILAC_CONTRACT_VERSION = 1

# Default fan-out policy version. Bump explicitly when the fan-out rules
# change; the ledger keys on it so replays across policy versions stay
# distinct (R4 / contract_version doctrine).
FANOUT_POLICY_VERSION = 1

# Lifecycle receipt kinds conduit may emit into the canonical stream
# (Q3 kind vocabulary; admission stays PEB-owned and is NOT in this set).
LIFECYCLE_KINDS = (
    "plan_create", "planning", "implementation", "review", "review_pass",
    "review_reject", "critique", "critique_pass", "critique_reject",
    "block", "hold", "ccnf_execution", "requeued", "api_limit",
    "abandoned", "cancelled", "plan_block",
)

# Q3 kind-scoped grants — seeded in V139 and enforced by the DB trigger.
# The Python execution worker is a registered producer for lifecycle kinds
# until C3 cutover redirects it; conduit-mcp owns lifecycle receipts
# end-to-end after cutover.
PRODUCER_CONDUIT_MCP = "conduit-mcp"
PRODUCER_EXECUTION_WORKER = "nexus-execution-worker"

RECEIPT_KIND_BY_TYPE = {
    "PLAN_CREATE": "plan_create", "PLANNING": "planning",
    "IMPLEMENTATION": "implementation", "REVIEW": "review",
    "REVIEW_PASS": "review_pass", "REVIEW_REJECT": "review_reject",
    "CRITIQUE": "critique", "CRITIQUE_PASS": "critique_pass",
    "CRITIQUE_REJECT": "critique_reject", "BLOCK": "block",
    "HOLD": "hold", "CCNF_EXECUTION": "ccnf_execution",
    "REQUEUED": "requeued", "API_LIMIT": "api_limit",
    "ABANDONED": "abandoned", "CANCELLED": "cancelled",
    "PLAN_BLOCK": "plan_block",
}


def payload_fingerprint(payload: Dict[str, Any]) -> str:
    """Deterministic fingerprint over the canonical payload JSON.

    Sorted keys, no timestamps — the fingerprint identifies payload
    EQUIVALENCE for the R4 idempotency key, not write time.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def receipt_type_to_kind(receipt_type: str) -> Optional[str]:
    return RECEIPT_KIND_BY_TYPE.get(receipt_type)


class LilacPersistenceError(Exception):
    """Canonical persistence refused or conflicted."""


class LilacAdapter:
    """Writes the canonical Resolution stream (V139 shapes).

    Instantiated per-operation with a psycopg2 connection factory so tests
    can point it at a throwaway schema and the shadow seam can share the
    caller's connection/transaction.
    """

    def __init__(self, connection_factory, schema: str = "resolution",
                 producer_id: str = PRODUCER_EXECUTION_WORKER):
        self._conn_factory = connection_factory
        self._schema = schema
        self._producer_id = producer_id

    # ── low-level helpers ────────────────────────────────────────────

    def _q(self, sql: str) -> str:
        return sql.replace("%SCHEMA%", self._schema)

    @staticmethod
    def _classify_insert(rowcount: int, duplicate: bool = False) -> str:
        if duplicate:
            return "duplicate-equivalent"
        return "accepted" if rowcount else "duplicate-equivalent"

    # ── receipts ─────────────────────────────────────────────────────

    def insert_receipt(
        self,
        conn,
        *,
        kind: str,
        source_receipt_id: str,
        payload: Dict[str, Any],
        refs: Optional[Dict[str, Any]] = None,
        source_system: str = "conduit",
        contract_version: int = LILAC_CONTRACT_VERSION,
        manage_transaction: bool = True,
    ) -> Tuple[str, str]:
        """Insert into resolution.receipt (R4 + Q3 kind-scoped grants).

        Returns (outcome_class, receipt_id). outcome is one of:
        accepted · duplicate-equivalent · conflict · refused.

        Conflict semantics (R4, explicit — never silent): same
        (source_system, source_receipt_id) with a DIFFERENT fingerprint →
        LilacPersistenceError('conflict') with BOTH fingerprints recorded
        in the message for the refusal evidence trail. When
        ``manage_transaction`` is false, this method uses a savepoint and
        leaves commit/rollback ownership to the caller's transaction.
        """
        fp = payload_fingerprint(payload)
        cur = conn.cursor()
        savepoint = "conduit_lilac_insert_receipt"
        if not manage_transaction:
            cur.execute(f"SAVEPOINT {savepoint}")
        try:
            cur.execute(
                self._q(
                    """INSERT INTO %SCHEMA%.receipt
                       (producer_id, kind, source_system, source_receipt_id,
                        payload_fingerprint, payload, refs, contract_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id"""
                ),
                (self._producer_id, kind, source_system, source_receipt_id,
                 fp, json.dumps(payload), json.dumps(refs or {}), contract_version),
            )
            row = cur.fetchone()
            if manage_transaction:
                conn.commit()
            else:
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            return self._classify_insert(1), str(row[0])
        except Exception as exc:
            sqlstate = getattr(exc, "pgcode", None)
            if manage_transaction:
                conn.rollback()
            else:
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            if sqlstate == "P0004":
                # Kind-scoped producer grant refusal (DB-enforced, Q3).
                raise LilacPersistenceError(
                    f"refused: producer grant violation: {exc}"
                ) from exc
            # Distinguish duplicate-equivalent from conflicting replay (R4).
            dup = self._find_existing_receipt(
                conn, source_system, source_receipt_id, expect_fingerprint=None
            )
            if dup is not None:
                existing_fp, existing_id = dup
                if existing_fp == fp:
                    return "duplicate-equivalent", existing_id
                raise LilacPersistenceError(
                    "conflict: same source id, different payload — "
                    f"existing_fingerprint={existing_fp} incoming_fingerprint={fp}"
                ) from exc
            # Stage B (V143): the partial unique index on the admission txn
            # id also rejects replays that carry a NEW source id but the SAME
            # peb_transaction_id. Classify that replay against the indexed
            # surface (R4): identical payload → duplicate-equivalent (the
            # canonical row's id wins); different payload → conflict.
            if sqlstate == "23505" and kind == "admission":
                txn_id = payload.get("peb_transaction_id")
                if txn_id:
                    dup_txn = self._find_admission_by_txn(conn, str(txn_id))
                    if dup_txn is not None:
                        existing_fp, existing_id = dup_txn
                        if existing_fp == fp:
                            return "duplicate-equivalent", existing_id
                        raise LilacPersistenceError(
                            "conflict: same peb_transaction_id, different payload — "
                            f"existing_fingerprint={existing_fp} incoming_fingerprint={fp}"
                        ) from exc
            raise

    def _find_admission_by_txn(self, conn, txn_id: str) -> Optional[Tuple[str, str]]:
        """Canonical admission lookup by the Stage B indexed expression.

        Only meaningful when the txn-id unique index rejected an insert —
        i.e. some admission row already owns this peb_transaction_id.
        Returns (payload_fingerprint, id) of the oldest such row.
        """
        cur = conn.cursor()
        cur.execute(
            self._q(
                """SELECT payload_fingerprint, id FROM %SCHEMA%.receipt
                   WHERE kind='admission'
                     AND payload->>'peb_transaction_id'=%s
                   ORDER BY created_at ASC LIMIT 1"""
            ),
            (txn_id,),
        )
        row = cur.fetchone()
        conn.commit()
        return (str(row[0]), str(row[1])) if row else None

    def _find_existing_receipt(self, conn, source_system: str, source_receipt_id: str,
                               expect_fingerprint: Optional[str]) -> Optional[Tuple[str, str]]:
        cur = conn.cursor()
        cur.execute(
            self._q(
                """SELECT payload_fingerprint, id FROM %SCHEMA%.receipt
                   WHERE source_system=%s AND source_receipt_id=%s
                   ORDER BY created_at ASC LIMIT 1"""
            ),
            (source_system, source_receipt_id),
        )
        row = cur.fetchone()
        conn.commit()
        return (str(row[0]), str(row[1])) if row else None

    # ── tickets ──────────────────────────────────────────────────────

    def issue_ticket(
        self,
        conn,
        *,
        workflow_ref: str,
        role: str,
        position: int,
        predecessor_receipt_id: Optional[str],
        generation: int = 0,
        objective: str = "",
        contract_version: int = LILAC_CONTRACT_VERSION,
    ) -> Tuple[str, str]:
        """Issue a canonical ticket (R4 idempotency key
        (workflow_ref, role, position, generation))."""
        cur = conn.cursor()
        try:
            cur.execute(
                self._q(
                    """INSERT INTO %SCHEMA%.ticket
                       (workflow_ref, role, position, predecessor_receipt_id,
                        generation, objective, contract_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id"""
                ),
                (workflow_ref, role, position, predecessor_receipt_id,
                 generation, objective, contract_version),
            )
            row = cur.fetchone()
            conn.commit()
            return self._classify_insert(1), str(row[0])
        except Exception as exc:
            conn.rollback()
            cur2 = conn.cursor()
            cur2.execute(
                self._q(
                    """SELECT id FROM %SCHEMA%.ticket
                       WHERE workflow_ref=%s AND role=%s AND position=%s AND generation=%s"""
                ),
                (workflow_ref, role, position, generation),
            )
            row2 = cur2.fetchone()
            conn.commit()
            if row2:
                return "duplicate-equivalent", str(row2[0])
            raise

    # ── transitions + THE fan-out ledger ─────────────────────────────

    def record_transition(
        self,
        conn,
        *,
        ticket_id: str,
        from_status: str,
        to_status: str,
        input_receipt_id: Optional[str],
        outcome_class: str = "accepted",
        fanout_policy_version: int = FANOUT_POLICY_VERSION,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        cur = conn.cursor()
        try:
            cur.execute(
                self._q(
                    """INSERT INTO %SCHEMA%.ticket_transition
                       (ticket_id, input_receipt_id, from_status, to_status,
                        fanout_policy_version, outcome_class, payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id"""
                ),
                (ticket_id, input_receipt_id, from_status, to_status,
                 fanout_policy_version, outcome_class, json.dumps(payload or {})),
            )
            row = cur.fetchone()
            conn.commit()
            return self._classify_insert(1), str(row[0])
        except Exception as exc:
            conn.rollback()
            cur2 = conn.cursor()
            cur2.execute(
                self._q(
                    """SELECT id FROM %SCHEMA%.ticket_transition
                       WHERE ticket_id=%s AND from_status=%s AND to_status=%s
                         AND input_receipt_id IS NOT DISTINCT FROM %s
                         AND fanout_policy_version=%s"""
                ),
                (ticket_id, from_status, to_status, input_receipt_id, fanout_policy_version),
            )
            row2 = cur2.fetchone()
            conn.commit()
            if row2:
                return "duplicate-equivalent", str(row2[0])
            raise

    def apply_fanout(
        self,
        conn,
        *,
        input_receipt_id: str,
        kind: str,
        spawn_specs: List[Dict[str, Any]],
        completing_ticket_id: Optional[str] = None,
        from_status: str = "open",
        to_status: str = "closed",
        fan_out_policy_version: int = FANOUT_POLICY_VERSION,
    ) -> Tuple[str, List[str]]:
        """THE single receipt-to-ticket fan-out (C3).

        Position-aware: closes the completing ticket (optional) and spawns
        the next-position tickets atomically in ONE ledger row keyed
        (input_receipt_id, kind, fan_out_policy_version). Replay of the
        same input → duplicate-equivalent with no new tickets (the ledger
        row is the idempotency contract, R4).

        Returns (outcome, produced_ticket_ids).
        """
        cur = conn.cursor()
        try:
            produced_ids: List[str] = []
            produced_refs: List[Dict[str, Any]] = []
            if completing_ticket_id:
                cur.execute(
                    self._q(
                        """UPDATE %SCHEMA%.ticket SET status=%s, updated_at=now()
                           WHERE id=%s AND status<>%s RETURNING id"""
                    ),
                    (to_status, completing_ticket_id, to_status),
                )
                closed = cur.fetchone()
                if closed:
                    produced_ids.append(str(closed[0]))
                    produced_refs.append(
                        {"ticket_id": str(closed[0]), "action": "closed",
                         "from_status": from_status, "to_status": to_status}
                    )

            for spec in spawn_specs:
                outcome, tid = self.issue_ticket(
                    conn,
                    workflow_ref=spec["workflow_ref"],
                    role=spec["role"],
                    position=spec["position"],
                    predecessor_receipt_id=input_receipt_id,
                    generation=spec.get("generation", 0),
                    objective=spec.get("objective", ""),
                )
                if outcome == "accepted":
                    produced_ids.append(tid)
                produced_refs.append(
                    {"ticket_id": tid, "action": "spawned", "role": spec["role"],
                     "position": spec["position"], "outcome": outcome}
                )

            cur.execute(
                self._q(
                    """INSERT INTO %SCHEMA%.fanout_transition
                       (input_receipt_id, kind, fan_out_policy_version,
                        outcome, produced)
                       VALUES (%s,%s,%s,%s,%s)
                       RETURNING id"""
                ),
                (input_receipt_id, kind, fan_out_policy_version,
                 "spawned" if any(r["action"] == "spawned" and r["outcome"] == "accepted"
                                  for r in produced_refs) or
                       any(r["action"] == "closed" for r in produced_refs)
                 else "no-op",
                 json.dumps(produced_refs)),
            )
            cur.fetchone()
            conn.commit()
            return ("spawned" if produced_ids else "no-op"), produced_ids
        except Exception as exc:
            conn.rollback()
            cur2 = conn.cursor()
            cur2.execute(
                self._q(
                    """SELECT outcome, produced FROM %SCHEMA%.fanout_transition
                       WHERE input_receipt_id=%s AND kind=%s AND fan_out_policy_version=%s"""
                ),
                (input_receipt_id, kind, fan_out_policy_version),
            )
            row2 = cur2.fetchone()
            conn.commit()
            if row2:
                produced_json = row2[1]
                if isinstance(produced_json, str):
                    produced_json = json.loads(produced_json)
                return "duplicate-equivalent", [r["ticket_id"] for r in produced_json or []]
            raise


def shadow_write_enabled() -> bool:
    """C3 staging gate: shadow writes default OFF until C2 ratification."""
    return os.environ.get("CONDUIT_LILAC_SHADOW", "").strip() == "1"


def shadow_record_receipt(db, schema: str, receipt_row: Dict[str, Any],
                          force: bool = False, manage_transaction: bool = True) -> None:
    """Best-effort canonical shadow record for the C2 ratification evidence.

    ``db`` is the DBAdapter whose pooled connection is used. Call AFTER the
    legacy write has committed — the shadow record must never share or
    disturb the legacy transaction. NEVER raises into the caller: the
    legacy write path stays authoritative while staged. Failures are
    logged, counted as shadow-skip, and nothing is surfaced to callers.

    ``force=True`` (redirect=shadow stage) records unconditionally — the
    writer redirection design (record 281510c7) makes the canonical record
    part of the shadow stage itself, without flipping CONDUIT_LILAC_SHADOW.
    A caller holding a transaction-scoped plan lock may pass
    ``manage_transaction=False`` so this canonical write remains in that
    transaction until the lock-owning caller commits.
    """
    if not force and not shadow_write_enabled():
        return
    kind = receipt_row.get("kind")
    if not kind:
        return
    try:
        with db._get_connection() as conn:
            # DBAdapter yields a _ConnectionProxy; the LilacAdapter speaks
            # raw psycopg2 (cursor/commit/rollback). Unwrap when present.
            raw = getattr(conn, "_conn", conn)
            # F4 (architect review 761e6338): the declaring producer is
            # passed through UNFILTERED — the producer_registry grant
            # trigger is the per-write authority in BOTH modes. Any code-
            # side copy of the registry would silently misattribute writes
            # from future producers (the exact bug the whitelist tried to
            # fix); an unregistered producer is refused by the trigger
            # (P0004) and lands in the shadow-skip log — visible, not
            # silently mislabeled.
            declaring = (receipt_row.get("payload") or {}).get("producer_id")
            adapter = LilacAdapter(
                lambda: raw,
                schema=schema,
                producer_id=declaring or PRODUCER_EXECUTION_WORKER,
            )
            adapter.insert_receipt(
                raw,
                kind=kind,
                source_receipt_id=receipt_row["source_receipt_id"],
                payload=receipt_row["payload"],
                refs=receipt_row.get("refs"),
                source_system=receipt_row.get("source_system", "conduit"),
                manage_transaction=manage_transaction,
            )
    except Exception as exc:  # noqa: BLE001 — deliberately swallow (shadow mode)
        _log.warning("lilac shadow receipt skipped: %s", exc)
