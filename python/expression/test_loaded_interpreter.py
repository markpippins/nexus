"""E8.2 tests — Expression over a DB-loaded interpreter.

Uses the real DatabaseLoader with a fake pool reproducing the actual `fc`
gate proposition from the resolution DB (REJECTED disposition, one
assertion rule, two frame values: governed_reference migration_phase and
typed_scalar as_of_version), then verifies the identity seam and the full
round-trip through the Expression envelope.
"""
from __future__ import annotations

import asyncio
import unittest
import uuid

from expression.loaded_interpreter import (
    EXPRESSION_ID_PREFIX,
    LoadedInterpreter,
    loaded_population_fingerprint,
)
from expression.pipeline import build_expression_bundle


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
            # Must precede the frame_dimension check: the fdv query names
            # resolution.frame_dimension_value.
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


_FC_ID = "fc000000-0000-4000-8000-0000000000c1"
_RULE_ID = "d1a00000-0000-4000-8000-00000000c001"
_MIGRATION_DIM = "d1a00000-0000-4000-8000-000000000001"
_ASOF_DIM = "d1a00000-0000-4000-8000-000000000002"
_PRE_MIG = "bb229925-8ced-4753-9b28-55dab47e36bd"


def _build_tables() -> dict:
    prop_uuid = uuid.UUID(_FC_ID)
    rejected_disp_id = _uid()  # fc's stored disposition is REJECTED
    return {
        "disp_values": [
            {"id": _uid(), "value": "Asserted"},
            {"id": rejected_disp_id, "value": "Rejected"},
            {"id": _uid(), "value": "Pending"},
        ],
        "assertions": [{"proposition_id": _FC_ID, "rule_id": _RULE_ID, "rule_exists": _RULE_ID}],
        "frames": [
            {
                "id": _uid(),
                "proposition_id": _FC_ID,
                "dimension_id": _MIGRATION_DIM,
                "reference_value_id": _PRE_MIG,
                "scalar_value": None,
            },
            {
                "id": _uid(),
                "proposition_id": _FC_ID,
                "dimension_id": _ASOF_DIM,
                "reference_value_id": None,
                "scalar_value": "v1",
            },
        ],
        "propositions": [
            {
                "id": prop_uuid,
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


def _load_interpreter():
    """Build the interpreter with fc's assertion rule present (like E8.1 smoke)."""
    from solscript.database_loader import DatabaseLoader
    from solscript.interpreter import ResolutionInterpreter
    from solscript.models import Rule, RuleType, Severity

    interp = ResolutionInterpreter()
    # fc's assertion rule must exist in interpreter.rules; the real rule has
    # no expression in the DB (probe rule), so materialize it minimally.
    interp.rules[_RULE_ID] = Rule(
        id=_RULE_ID,
        name="fc_context_gate_probe",
        rule_type=RuleType.INVARIANT,
        expression=None,
        severity=Severity.HARD,
    )
    loader = DatabaseLoader(interp, _Pool(_build_tables()))
    asyncio.get_event_loop().run_until_complete(loader.load_frame_dimensions())
    asyncio.get_event_loop().run_until_complete(loader.load_propositions())
    return LoadedInterpreter(interp, loader_report=loader.load_report)


def _expression_bundle_for_fc():
    transcript = {
        "transcript_id": "e8-fixture-001",
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


class LoadedInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.loaded = _load_interpreter()
        self.bundle = _expression_bundle_for_fc()
        self.read_set = {"read_set_id": "e8-read-set", "fingerprint": "e8-read-set-sha256"}
        # The real fc proposition exists; force its assertion to be a probe
        # that rejects (rule has no expression → check_rule fails HARD).
        # This mirrors the live fc behavior: gate passes, probe fails.

    def test_loader_populates_fc_with_assertions_and_frames(self):
        fc = self.loaded.interpreter.propositions[_FC_ID]
        self.assertEqual(len(fc.assertions), 1)
        self.assertEqual(len(fc.frame_values), 2)

    def test_unregistered_candidate_is_pending_not_refused(self):
        envelope = self.loaded.evaluate_bundle(
            self.bundle, read_set=self.read_set, ontology_revision="ontology-e8"
        )
        result = envelope["results"][0]
        self.assertEqual(result["disposition"], "pending")
        self.assertEqual(result["reason_code"], "proposition_not_registered")

    def test_registered_candidate_evaluates_through_seam(self):
        self.loaded.register_candidate("digest-fc-001", _FC_ID)
        envelope = self.loaded.evaluate_bundle(
            self.bundle,
            read_set=self.read_set,
            ontology_revision="ontology-e8",
            context={"migration_phase": "pre_migration", "as_of_version": "v1"},
        )
        result = envelope["results"][0]
        # scoped + probe rule (HARD, no expression) fails → REJECTED
        self.assertEqual(result["disposition"], "rejected")
        self.assertEqual(envelope["loaded_population_fingerprint"], self.loaded.population_fingerprint())
        self.assertEqual(
            envelope["registrations"],
            [{"expression_id": f"{EXPRESSION_ID_PREFIX}digest-fc-001", "db_proposition_id": _FC_ID}],
        )

    def test_registration_requires_loaded_proposition(self):
        with self.assertRaises(ValueError):
            self.loaded.register_candidate("digest-xyz", "00000000-0000-0000-0000-000000000000")

    def test_no_silent_id_rewrite(self):
        # A candidate ID that is not registered never evaluates as the DB
        # proposition — the seam maps, it does not guess.
        envelope = self.loaded.evaluate_bundle(
            self.bundle, read_set=self.read_set, ontology_revision="ontology-e8"
        )
        self.assertEqual(envelope["results"][0]["reason_code"], "proposition_not_registered")
        # And even a candidate whose ID *equals* the DB UUID goes through the
        # seam explicitly: it is pending until registered.
        bundle = _expression_bundle_for_fc()
        bundle["proposition_candidates"] = [
            {**bundle["proposition_candidates"][0], "proposition_id": _FC_ID}
        ]
        envelope2 = self.loaded.evaluate_bundle(
            bundle, read_set=self.read_set, ontology_revision="ontology-e8"
        )
        self.assertEqual(envelope2["results"][0]["reason_code"], "proposition_not_registered")

    def test_population_fingerprint_is_stable_and_sensitive(self):
        fp1 = self.loaded.population_fingerprint()
        fp2 = loaded_population_fingerprint(self.loaded.population_snapshot())
        self.assertEqual(fp1, fp2)
        # Mutating the population changes the fingerprint.
        fc = self.loaded.interpreter.propositions[_FC_ID]
        fc.assertions = []
        fp3 = self.loaded.population_fingerprint()
        self.assertNotEqual(fp1, fp3)

    def test_context_gate_outcomes_surface_through_envelope(self):
        self.loaded.register_candidate("digest-fc-001", _FC_ID)
        # Missing as_of_version → context_required → unevaluable (E7 mapping)
        envelope = self.loaded.evaluate_bundle(
            self.bundle,
            read_set=self.read_set,
            ontology_revision="ontology-e8",
            context={"migration_phase": "pre_migration"},
        )
        self.assertEqual(envelope["results"][0]["disposition"], "unevaluable")
        # Mismatched scalar → context_mismatch → refused (E7 mapping)
        envelope2 = self.loaded.evaluate_bundle(
            self.bundle,
            read_set=self.read_set,
            ontology_revision="ontology-e8",
            context={"migration_phase": "pre_migration", "as_of_version": "v9"},
        )
        self.assertEqual(envelope2["results"][0]["disposition"], "refused")


if __name__ == "__main__":
    unittest.main()
