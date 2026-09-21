"""Hermetic E6 persistence adapter tests (V139 contract parity).

These tests exercise the adapter logic without a database: the connection
is a fake recording cursor that reproduces the real DB behaviors the
adapter must distinguish (accepted insert, R4 idempotency duplicate,
conflicting fingerprint, and producer grant refusal P0004). Live round-trip
coverage belongs to the C3 conformance suite which already owns the
throwaway-schema pattern.
"""
from __future__ import annotations

import json
import unittest
from typing import Any

from expression.e5 import build_e5_slice
from expression.evaluator import evaluate_bundle
from expression.persistence import (
    EXPRESSION_KIND,
    EXPRESSION_PRODUCER_ID,
    ExpressionPersistenceError,
    ResolutionReceiptWriter,
    _payload_fingerprint,
    receipt_payload,
    receipt_source_id,
)
from expression.pipeline import build_expression_bundle


class _FakeCursor:
    def __init__(self, conn: "_FakeConnection"):
        self._conn = conn

    def execute(self, sql: str, params: tuple | None = None):
        self._conn.executions.append((sql.strip(), params))
        if "INSERT INTO" in sql:
            if self._conn.insert_error is not None:
                error = self._conn.insert_error
                self._conn.insert_error = None
                raise error
            self._conn.inserted.append(params)
            self._conn.last_return = ("row-uuid",)
        elif "SELECT payload_fingerprint" in sql:
            self._conn.last_return = (self._conn.stored_fingerprint,)

    def fetchone(self):
        return self._conn.last_return

    def close(self):
        pass


class _FakeConnection:
    def __init__(self):
        self.executions: list[tuple[str, tuple | None]] = []
        self.inserted: list[tuple | None] = []
        self.insert_error: Exception | None = None
        self.stored_fingerprint: str | None = None
        self.last_return: tuple | None = None
        self.rollbacks = 0

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        pass


class _FakeFactory:
    def __init__(self, conn: _FakeConnection):
        self._conn = conn

    def __call__(self):
        return self

    def __enter__(self):
        return self._conn

    def __exit__(self, *_args):
        return False


class ExpressionPersistenceTests(unittest.TestCase):
    def setUp(self):
        transcript = {
            "transcript_id": "e6-fixture-001",
            "turns": [
                {"role": "user", "content": "Review PR #251."},
                {"role": "assistant", "content": "The Architect approved the pre-stage."},
            ],
        }
        self.bundle = build_expression_bundle(transcript)
        self.read_set = {"read_set_id": "e6-read-set", "fingerprint": "e6-read-set-sha256"}
        self.evaluation = evaluate_bundle(
            self.bundle,
            read_set=self.read_set,
            evaluator=lambda _candidate, _request: {"disposition": "advisory"},
            evaluator_revision="solscript-e6",
            ontology_revision="ontology-e6",
            authority_owner="resolution",
        )
        self.artifact = build_e5_slice(
            self.bundle, self.evaluation, read_set=self.read_set, source_run_id="run-e6-001"
        )

    def _writer(self, conn: _FakeConnection):
        return ResolutionReceiptWriter(_FakeFactory(conn))

    def test_receipt_payload_is_stable_and_non_authoritative(self):
        receipt = self.artifact["canonical_receipts"][0]
        payload = receipt_payload(self.artifact, receipt)
        self.assertEqual(payload["producer_id"], EXPRESSION_PRODUCER_ID)
        self.assertEqual(payload["authority_status"], "evaluation_only")
        again = receipt_payload(self.artifact, receipt)
        self.assertEqual(json.dumps(payload, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_source_receipt_id_is_deterministic(self):
        receipt = self.artifact["canonical_receipts"][0]
        first = receipt_source_id(self.artifact, receipt)
        second = receipt_source_id(self.artifact, receipt)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 48)

    def test_only_expression_kind_is_writable(self):
        conn = _FakeConnection()
        writer = self._writer(conn)
        with self.assertRaises(ExpressionPersistenceError):
            writer.insert_receipt(
                conn, kind="admission", source_receipt_id="x", payload={}
            )

    def test_accept_and_duplicate_equivalent_outcomes(self):
        conn = _FakeConnection()
        writer = self._writer(conn)
        payload = receipt_payload(self.artifact, self.artifact["canonical_receipts"][0])
        source_id = receipt_source_id(self.artifact, self.artifact["canonical_receipts"][0])

        outcome, receipt_id = writer.insert_receipt(
            conn, kind=EXPRESSION_KIND, source_receipt_id=source_id, payload=payload
        )
        self.assertEqual(outcome, "accepted")
        self.assertEqual(receipt_id, "row-uuid")
        self.assertEqual(len(conn.inserted), 1)
        producer, kind, source_system = conn.inserted[0][:3]
        self.assertEqual(producer, EXPRESSION_PRODUCER_ID)
        self.assertEqual(kind, EXPRESSION_KIND)
        self.assertEqual(source_system, "expression")

        # Replay: unique violation + same stored fingerprint -> duplicate-equivalent (R4).
        class _UniqueViolation(Exception):
            pass

        conn.stored_fingerprint = _payload_fingerprint(payload)
        conn.insert_error = _UniqueViolation(
            'duplicate key value violates unique constraint "uq_resolution_receipt_idem"'
        )
        outcome2, receipt_id2 = writer.insert_receipt(
            conn, kind=EXPRESSION_KIND, source_receipt_id=source_id, payload=payload
        )
        self.assertEqual(outcome2, "duplicate-equivalent")
        self.assertEqual(receipt_id2, source_id)
        self.assertEqual(len(conn.inserted), 1)

    def test_conflicting_fingerprint_fails_closed(self):
        conn = _FakeConnection()
        writer = self._writer(conn)
        payload = receipt_payload(self.artifact, self.artifact["canonical_receipts"][0])
        source_id = receipt_source_id(self.artifact, self.artifact["canonical_receipts"][0])

        class _UniqueViolation(Exception):
            pass

        conn.insert_error = _UniqueViolation(
            'duplicate key value violates unique constraint "uq_resolution_receipt_idem"'
        )
        # Give the fake a stored fingerprint helper path via the real method.
        writer._stored_fingerprint = lambda _conn, _sid: "different-fingerprint"  # type: ignore[method-assign]
        with self.assertRaises(ExpressionPersistenceError) as caught:
            writer.insert_receipt(
                conn, kind=EXPRESSION_KIND, source_receipt_id=source_id, payload=payload
            )
        self.assertIn("conflict", str(caught.exception))
        self.assertIn("different-fingerprint", str(caught.exception))
        self.assertEqual(conn.rollbacks, 1)

    def test_grant_refusal_is_explicit_refused(self):
        conn = _FakeConnection()
        writer = self._writer(conn)
        payload = receipt_payload(self.artifact, self.artifact["canonical_receipts"][0])

        class _P0004(Exception):
            pass

        conn.insert_error = _P0004(
            "producer grant refused: producer=expression-pipeline kind=expression_evaluation"
        )
        with self.assertRaises(ExpressionPersistenceError) as caught:
            writer.insert_receipt(
                conn,
                kind=EXPRESSION_KIND,
                source_receipt_id="grant-test",
                payload=payload,
            )
        self.assertTrue(str(caught.exception).startswith("refused"))
        self.assertEqual(conn.rollbacks, 1)

    def test_record_artifact_reports_every_outcome_class(self):
        conn = _FakeConnection()
        writer = self._writer(conn)
        report = writer.record_artifact(self.artifact)
        self.assertEqual(report["artifact_fingerprint"], self.artifact["artifact_fingerprint"])
        self.assertTrue(report["accepted"])
        self.assertEqual(
            len(report["accepted"]), len(self.artifact["canonical_receipts"])
        )


if __name__ == "__main__":
    unittest.main()
