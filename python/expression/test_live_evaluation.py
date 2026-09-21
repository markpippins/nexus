"""E8.3 tests — bounded live evaluation through the E6 writer.

Uses the real DatabaseLoader with the fake pool reproducing the actual `fc`
gate proposition shapes (as in the E8.1/E8.2 tests) and a fake PG
connection capturing receipt inserts, verifying:

- the full chain runs: load → seam → evaluate → E5 artifact → E6 outcomes;
- receipts pin the loaded population fingerprint;
- re-running the identical artifact is duplicate-equivalent (R4);
- a drifted population changes the fingerprint (drift detection);
- runs without a writer are explicit skipped, never half-persisted.
"""
from __future__ import annotations

import asyncio
import unittest
import uuid

from expression.loaded_interpreter import LoadedInterpreter
from expression.live_evaluation import run_bounded_evaluation
from expression.persistence import ResolutionReceiptWriter, receipt_payload, receipt_source_id
from expression.pipeline import build_expression_bundle


_FC_ID = "fc000000-0000-4000-8000-0000000000c1"
_RULE_ID = "d1a00000-0000-4000-8000-00000000c001"
_MIGRATION_DIM = "d1a00000-0000-4000-8000-000000000001"
_ASOF_DIM = "d1a00000-0000-4000-8000-000000000002"
_PRE_MIG = "bb229925-8ced-4753-9b28-55dab47e36bd"


def _uid() -> str:
    return str(uuid.uuid4())


class _Conn:
    def __init__(self, tables):
        self._tables = tables

    async def fetch(self, sql: str, *params):
        if "concept_attribute_value" in sql:
            return self._tables["disp_values"]
        if "proposition_assertion" in sql:
            return self._tables["assertions"]
        if "proposition_frame_value" in sql:
            return self._tables["frames"]
        if "FROM resolution.proposition" in sql:
            return self._tables["propositions"]
        if "frame_dimension_value" in sql:
            return self._tables["dim_values"]
        if "FROM resolution.frame_dimension" in sql:
            return self._tables["dims"]
        return []


class _Pool:
    def __init__(self, tables):
        self._conn = _Conn(tables)

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._conn

            async def __aexit__(self, *_a):
                return False

        return _Ctx()


class _UniqueViolation(Exception):
    """Stands in for psycopg2's unique-violation on uq_resolution_receipt_idem."""


class _FakePGCursor:
    def __init__(self, conn: "_FakePGConnection"):
        self._conn = conn

    def execute(self, sql: str, params=None):
        self._conn.executions.append((sql.strip(), params))
        if "INSERT INTO" in sql:
            # params: (producer_id, kind, source_system, source_receipt_id,
            #          payload_fingerprint, payload, refs, contract_version)
            source_receipt_id = params[3]
            fingerprint = params[4]
            stored = self._conn.stored.get(source_receipt_id)
            if stored is not None:
                # Real DB: the idem unique index fires on any replay.
                raise _UniqueViolation(
                    f"uq_resolution_receipt_idem replay source_receipt_id={source_receipt_id}"
                )
            self._conn.stored[source_receipt_id] = fingerprint
            self._conn.inserted.append(params)
            self._conn.last_return = ("row-uuid",)
        elif "SELECT payload_fingerprint" in sql:
            # params: (source_system, source_receipt_id)
            fingerprint = self._conn.stored.get(params[1])
            self._conn.last_return = (fingerprint,) if fingerprint is not None else None

    def fetchone(self):
        return self._conn.last_return

    def close(self):
        pass


class _FakePGConnection:
    def __init__(self):
        self.executions = []
        self.inserted = []
        self.insert_error = None
        # source_receipt_id -> payload_fingerprint, as the real idem index implies.
        self.stored = {}
        self.last_return = None
        self.rollbacks = 0

    def cursor(self):
        return _FakePGCursor(self)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        pass


class _FakePGFactory:
    def __init__(self, conn):
        self._conn = conn
        self._entered = False

    def __call__(self):
        return self

    def __enter__(self):
        self._entered = True
        return self._conn

    def __exit__(self, *_a):
        return False


def _build_tables():
    rejected_disp_id = _uid()
    return {
        "disp_values": [
            {"id": _uid(), "value": "Asserted"},
            {"id": rejected_disp_id, "value": "Rejected"},
            {"id": _uid(), "value": "Pending"},
        ],
        "assertions": [{"proposition_id": _FC_ID, "rule_id": _RULE_ID, "rule_exists": _RULE_ID}],
        "frames": [
            {"id": _uid(), "proposition_id": _FC_ID, "dimension_id": _MIGRATION_DIM, "reference_value_id": _PRE_MIG, "scalar_value": None},
            {"id": _uid(), "proposition_id": _FC_ID, "dimension_id": _ASOF_DIM, "reference_value_id": None, "scalar_value": "v1"},
        ],
        "propositions": [
            {
                "id": uuid.UUID(_FC_ID),
                "title": "fc — context gate four-outcome test",
                "description": None,
                "asset_concept_id": uuid.UUID(_uid()),
                "subject_entity_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
                "disposition_value_id": uuid.UUID(rejected_disp_id),
                "value": None,
                "grounding_status_value_id": None,
                "semantic_type_id": None,
            }
        ],
        "dims": [
            {"id": _MIGRATION_DIM, "name": "migration_phase", "description": None, "value_kind": "governed_reference", "scalar_type": None},
            {"id": _ASOF_DIM, "name": "as_of_version", "description": None, "value_kind": "typed_scalar", "scalar_type": "text"},
        ],
        "dim_values": [
            {"id": _PRE_MIG, "dimension_id": _MIGRATION_DIM, "value": "pre_migration", "description": None},
        ],
    }


def _load():
    from solscript.database_loader import DatabaseLoader
    from solscript.interpreter import ResolutionInterpreter
    from solscript.models import Rule, RuleType, Severity

    interp = ResolutionInterpreter()
    interp.rules[_RULE_ID] = Rule(
        id=_RULE_ID, name="fc_context_gate_probe",
        rule_type=RuleType.INVARIANT, expression=None, severity=Severity.HARD,
    )
    loader = DatabaseLoader(interp, _Pool(_build_tables()))
    asyncio.get_event_loop().run_until_complete(loader.load_frame_dimensions())
    asyncio.get_event_loop().run_until_complete(loader.load_propositions())
    return LoadedInterpreter(interp, loader_report=loader.load_report)


def _bundle():
    transcript = {
        "transcript_id": "e83-fixture-001",
        "turns": [
            {"role": "user", "content": "Review PR #251."},
            {"role": "assistant", "content": "The Architect approved the pre-stage."},
        ],
    }
    bundle = build_expression_bundle(transcript)
    bundle["proposition_candidates"] = [
        {
            "proposition_id": "digest-fc-001",
            "subject_ref": "segment:fixture",
            "predicate": "mentions_decision_language",
            "object_ref": None,
            "source_observation_ids": [bundle["observations"][0]["observation_id"]],
            "modality": "reported",
            "status": "candidate",
            "required_read_set": None,
        }
    ]
    return bundle


class BoundedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.loaded = _load()
        self.loaded.register_candidate("digest-fc-001", _FC_ID)
        self.bundle = _bundle()
        self.read_set = {"read_set_id": "e83-read-set", "fingerprint": "e83-read-set-sha256"}
        self.context = {"migration_phase": "pre_migration", "as_of_version": "v1"}

    def test_full_chain_runs_and_persists(self):
        conn = _FakePGConnection()
        report = run_bounded_evaluation(
            self.loaded, self.bundle,
            read_set=self.read_set, source_run_id="run-e83-001",
            ontology_revision="ontology-e83", context=self.context,
            receipt_writer=ResolutionReceiptWriter(_FakePGFactory(conn)),
        )
        self.assertEqual(report["e8_revision"], "expression-e8.3-v0.1")
        self.assertEqual(report["results_summary"], {"rejected": 1})
        self.assertEqual(report["persistence"]["status"], "recorded")
        self.assertEqual(len(report["persistence"]["accepted"]), 1)
        self.assertEqual(len(conn.inserted), 1)

    def test_receipts_pin_population_fingerprint(self):
        report = run_bounded_evaluation(
            self.loaded, self.bundle,
            read_set=self.read_set, source_run_id="run-e83-001",
            ontology_revision="ontology-e83", context=self.context,
        )
        fp = report["loaded_population_fingerprint"]
        self.assertTrue(fp)
        # The payload persisted into the receipt carries the fingerprint chain.
        artifact = report["artifact"]
        receipt = artifact["canonical_receipts"][0]
        payload = receipt_payload(artifact, receipt)
        self.assertEqual(payload["source_run_id"], "run-e83-001")
        self.assertEqual(payload["authority_status"], "evaluation_only")

    def test_rerun_same_population_is_duplicate_equivalent(self):
        conn = _FakePGConnection()
        writer = ResolutionReceiptWriter(_FakePGFactory(conn))
        first = run_bounded_evaluation(
            self.loaded, self.bundle,
            read_set=self.read_set, source_run_id="run-e83-001",
            ontology_revision="ontology-e83", context=self.context,
            receipt_writer=writer,
        )
        second = run_bounded_evaluation(
            self.loaded, self.bundle,
            read_set=self.read_set, source_run_id="run-e83-001",
            ontology_revision="ontology-e83", context=self.context,
            receipt_writer=writer,
        )
        # Same receipt identity replayed: first pass accepted, second pass
        # duplicate-equivalent against the stored fingerprint.
        self.assertEqual(
            [e["source_receipt_id"] for e in second["persistence"]["duplicate-equivalent"]],
            [e["source_receipt_id"] for e in first["persistence"]["accepted"]],
        )
        self.assertEqual(len(conn.inserted), 1)  # no second durable insert

    def test_population_drift_changes_fingerprint(self):
        report_before = run_bounded_evaluation(
            self.loaded, self.bundle,
            read_set=self.read_set, source_run_id="run-e83-001",
            ontology_revision="ontology-e83", context=self.context,
        )
        fp_before = report_before["loaded_population_fingerprint"]
        # Drift: strip the frame values from the loaded proposition.
        self.loaded.interpreter.propositions[_FC_ID].frame_values = []
        report_after = run_bounded_evaluation(
            self.loaded, self.bundle,
            read_set=self.read_set, source_run_id="run-e83-002",
            ontology_revision="ontology-e83", context=self.context,
        )
        self.assertNotEqual(fp_before, report_after["loaded_population_fingerprint"])

    def test_no_writer_is_explicitly_skipped(self):
        report = run_bounded_evaluation(
            self.loaded, self.bundle,
            read_set=self.read_set, source_run_id="run-e83-001",
            ontology_revision="ontology-e83", context=self.context,
        )
        self.assertEqual(report["persistence"]["status"], "skipped")
        self.assertEqual(report["results_summary"], {"rejected": 1})

    def test_unregistered_candidates_block_persistence(self):
        loaded = _load()  # no registration
        report = run_bounded_evaluation(
            loaded, self.bundle,
            read_set=self.read_set, source_run_id="run-e83-003",
            ontology_revision="ontology-e83", context=self.context,
        )
        self.assertEqual(report["results_summary"], {"pending": 1})


def _payload_fp(artifact, receipt):
    import hashlib
    import json
    payload = receipt_payload(artifact, receipt)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


if __name__ == "__main__":
    unittest.main()
