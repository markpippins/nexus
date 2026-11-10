#!/usr/bin/env python3
"""Conformance proof: Shrapnel polymorphism + storage-adapter parity.

Blueprint plan 8261656. Pure/hermetic: no database, no network, no production
collection. The disposable PostgreSQL JSONB proof is a TEMP-table file under
`typescript/shrapnel/conformance/` (never a migration); the ephemeral MongoDB
proof is the in-memory `MockMongoAdapter`.

Run:
    python3 -m pytest bin/tests/test_shrapnel_storage_conformance.py -q
"""

from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python" / "SOLScript"))
sys.path.insert(0, str(REPO / "python"))

from expression import shrapnel_conformance as bridge  # noqa: E402
from solscript.adapters import shrapnel_conformance as core  # noqa: E402
from solscript.adapters.shrapnel_conformance import (  # noqa: E402
    CONFORMANCE_REVISION,
    DISPATCH_FIELDS,
    AuthorityPolicy,
    FieldStatus,
    MockJsonbAdapter,
    MockMongoAdapter,
    StereotypeCatalog,
    TypedValue,
    evaluate_materialization,
    minimal_fallback_descriptor,
    revision_defs_from_fixture,
    semantic_fingerprint,
)

FIXTURE_PATH = REPO / "docs" / "shrapnel" / "fixtures" / "conformance-fixtures.json"
PROOF_SQL = REPO / "typescript" / "shrapnel" / "conformance" / "pg_jsonb_proof.sql"
TS_MIRROR = REPO / "typescript" / "solscript" / "src" / "shrapnel-conformance.ts"
DOC = REPO / "docs" / "shrapnel" / "conformance-matrix.md"
MIGRATIONS = REPO / "typescript" / "shrapnel" / "migrations"

FIXTURE = json.loads(FIXTURE_PATH.read_text())
CATALOG = StereotypeCatalog(revision_defs_from_fixture(FIXTURE))
INSTANCES = {entry["name"]: entry for entry in FIXTURE["instances"]}

WR = "work_request_tester_attestation"
DOCUMENT = "document"
WR_INSTANCE = "tester_attestation"
DOC_INSTANCE = "document"


def adapter_for(instance: dict, policy: AuthorityPolicy | None = None):
    cls = MockJsonbAdapter if instance["materialization"] == "postgres_jsonb" else MockMongoAdapter
    return cls(CATALOG, policy)


def descriptor_for(instance: dict, policy: AuthorityPolicy | None = None):
    adapter = adapter_for(instance, policy)
    object_id = adapter.write(instance["document"])
    return adapter, adapter.descriptor(object_id)


def inst(workload: str, materialization: str) -> dict:
    return INSTANCES[f"{workload}_{materialization}"]


def pair(workload: str):
    return (inst(workload, "postgres_jsonb"), inst(workload, "mongo_document"))


class TestFixture(unittest.TestCase):
    def test_fixture_is_bounded_and_declares_both_materializations(self):
        self.assertTrue(FIXTURE["bounded"])
        names = {revision.stereotype_name for revision in CATALOG.revisions()}
        self.assertEqual(
            names,
            {
                "work_request",
                "work_request_dispatchable",
                WR,
                DOCUMENT,
            },
        )
        materializations = {entry["materialization"] for entry in FIXTURE["instances"]}
        self.assertEqual(materializations, {"postgres_jsonb", "mongo_document"})
        # Same logical identity/lineage/timestamps are expressed twice, once
        # per store. The Mongo wire shape splits dispatch members into their
        # own block, so full value equality is asserted on the normalized
        # descriptor in TestAdapterParity, not on the raw wire shape.
        for workload in (WR_INSTANCE, DOC_INSTANCE):
            pg, mongo = pair(workload)
            self.assertEqual(pg["document"]["lineage"], mongo["document"]["lineage"])
            self.assertEqual(pg["document"]["timestamps"], mongo["document"]["timestamps"])
            pg_doc, mongo_doc = pg["document"], mongo["document"]
            pg_values = pg_doc["values"]
            mongo_values = {**mongo_doc.get("doc", {}), **mongo_doc.get("dispatch", {})}
            self.assertEqual(set(pg_values), set(mongo_values))


class TestEvaluationModelBridge(unittest.TestCase):
    def test_bridge_exposes_identity_revision_contract_origin_and_typed_semantics(self):
        _, descriptor = descriptor_for(inst(WR_INSTANCE, "postgres_jsonb"))
        model = bridge.model_from_descriptor(descriptor)

        self.assertEqual(model.type_id, "shrapnel:stereotype:" + WR)
        self.assertEqual(model.stereotype_revision, 3)
        self.assertEqual(model.stereotype_version, 1)
        self.assertEqual(model.contract_fingerprint, descriptor.contract.contract_fingerprint)

        payload = bridge.bridge_descriptor(descriptor)
        self.assertEqual(payload["type_identity"]["type_id"], model.type_id)
        self.assertEqual(payload["type_identity"]["stereotype_revision"], 3)
        self.assertIn("effective_contract", payload)
        self.assertIn("inherited_field_names", payload)
        self.assertIn("typed_values", payload)

        # Inherited field origin: the leaf revision does not redeclare these.
        self.assertEqual(model.origin("request_kind").origin_revision, 1)
        self.assertTrue(model.origin("request_kind").inherited)
        self.assertEqual(model.origin("dispatch_lease_until").origin_revision, 2)
        self.assertTrue(model.origin("dispatch_lease_until").inherited)
        self.assertEqual(model.origin("attestation_id").origin_revision, 3)
        self.assertFalse(model.origin("attestation_id").inherited)
        self.assertEqual(
            set(model.inherited_field_names()),
            {"request_kind", "lineage_ref", "dispatch_lease_until"},
        )

        # Typed semantics: declared-but-absent optional fields are ABSENT, not null.
        self.assertEqual(model.typed_semantics()["attestation_payload"], "governed")
        self.assertEqual(model.typed_semantics()["attestation_evidence_ref"], "absent")
        self.assertEqual(model.typed_semantics()["request_kind"], "absent")

        disposition = bridge.evaluate_required_fields(model)
        self.assertEqual(disposition["disposition"], "asserted")
        self.assertIn("attestation_id", disposition["origin_attribution"])

    def test_document_scenario_uses_its_own_root_contract(self):
        _, descriptor = descriptor_for(inst(DOC_INSTANCE, "postgres_jsonb"))
        model = bridge.model_from_descriptor(descriptor)
        self.assertEqual(model.type_id, "shrapnel:stereotype:document")
        self.assertEqual(model.inherited_field_names(), ())
        self.assertEqual(model.origin("body").origin_revision, 4)


class TestAdapterParity(unittest.TestCase):
    def test_materializations_produce_identical_semantic_descriptors(self):
        for workload in (WR_INSTANCE, DOC_INSTANCE):
            with self.subTest(workload=workload):
                _, pg = descriptor_for(pair(workload)[0])
                _, mongo = descriptor_for(pair(workload)[1])
                self.assertEqual(pg.identity, mongo.identity)
                self.assertEqual(pg.contract, mongo.contract)
                self.assertEqual(pg.lineage, mongo.lineage)
                self.assertEqual(pg.timestamps, mongo.timestamps)
                self.assertEqual(pg.fields, mongo.fields)
                self.assertEqual(semantic_fingerprint(pg), semantic_fingerprint(mongo))
                # Type identity is storage-independent.
                self.assertNotIn("postgres", pg.identity.type_id)
                self.assertNotIn("mongo", pg.identity.type_id)
                surface = json.dumps(pg.as_semantic_dict(), sort_keys=True)
                self.assertNotIn("postgres_jsonb", surface)
                self.assertNotIn("mongo_document", surface)

    def test_evaluation_parity_across_materializations(self):
        for workload in (WR_INSTANCE, DOC_INSTANCE):
            with self.subTest(workload=workload):
                pg_instance, mongo_instance = pair(workload)
                pg_result = evaluate_materialization(adapter_for(pg_instance), pg_instance["document"])
                mongo_result = evaluate_materialization(
                    adapter_for(mongo_instance), mongo_instance["document"]
                )
                self.assertEqual(pg_result.disposition, "asserted")
                self.assertEqual(mongo_result.disposition, "asserted")
                self.assertEqual(pg_result.fingerprint, mongo_result.fingerprint)

                pg_model = bridge.model_from_descriptor(pg_result.descriptor)
                mongo_model = bridge.model_from_descriptor(mongo_result.descriptor)
                self.assertEqual(
                    bridge.evaluation_fingerprint(pg_model),
                    bridge.evaluation_fingerprint(mongo_model),
                )
                pg_disposition = bridge.evaluate_required_fields(pg_model)
                mongo_disposition = bridge.evaluate_required_fields(mongo_model)
                self.assertEqual(pg_disposition["disposition"], mongo_disposition["disposition"])
                self.assertEqual(
                    pg_disposition["origin_attribution"],
                    mongo_disposition["origin_attribution"],
                )
                self.assertEqual(pg_disposition["fingerprint"], mongo_disposition["fingerprint"])

    def test_normalizer_has_no_backend_branch(self):
        source = inspect.getsource(core)
        start = source.index("def normalize_instance(")
        end = source.index("def semantic_fingerprint(")
        body = source[start:end]
        for token in ("postgres", "mongo", "jsonb", "Mock"):
            self.assertNotIn(token.lower(), body.lower())


class TestRoundTrip(unittest.TestCase):
    def test_round_trip_preserves_contract_typing_lineage_and_timestamps(self):
        for workload in (WR_INSTANCE, DOC_INSTANCE):
            with self.subTest(workload=workload):
                for instance in pair(workload):
                    adapter = adapter_for(instance)
                    object_id = adapter.write(instance["document"])
                    first = adapter.descriptor(object_id)
                    raw = adapter.read(object_id)
                    second_adapter = adapter_for(instance)
                    second_id = second_adapter.write(adapter.encode(raw))
                    second = second_adapter.descriptor(second_id)

                    self.assertEqual(first.identity, second.identity)
                    self.assertEqual(first.contract, second.contract)
                    self.assertEqual(first.lineage, second.lineage)
                    self.assertEqual(first.timestamps, second.timestamps)
                    self.assertEqual(first.fields, second.fields)
                    self.assertEqual(semantic_fingerprint(first), semantic_fingerprint(second))

    def test_round_trip_fingerprint_is_stable_and_parity_preserving(self):
        pg_instance, mongo_instance = pair(WR_INSTANCE)
        pg = adapter_for(pg_instance)
        pg_id = pg.write(pg_instance["document"])
        mongo = adapter_for(mongo_instance)
        # PostgreSQL -> MongoDB -> PostgreSQL round trip.
        mongo_id = mongo.write(mongo.encode(pg.read(pg_id)))
        pg_again = adapter_for(pg_instance)
        pg_again_id = pg_again.write(pg_again.encode(mongo.read(mongo_id)))
        self.assertEqual(
            semantic_fingerprint(pg.descriptor(pg_id)),
            semantic_fingerprint(pg_again.descriptor(pg_again_id)),
        )
        # Lifecycle members survive in the typed descriptor view.
        lifecycle = pg.descriptor(pg_id).lifecycle()
        for name in ("request_id", "status", "dispatch_state", "attestation_id"):
            self.assertIs(lifecycle[name].status, FieldStatus.GOVERNED)


class TestUnknownSemantics(unittest.TestCase):
    def test_typed_unknown_unavailable_absent_and_governed_null(self):
        unknown = FIXTURE["unknown_semantics"]
        adapter = adapter_for(unknown)
        object_id = adapter.write(unknown["document"])
        descriptor = adapter.descriptor(
            object_id, unavailable_fields=unknown["unavailable_fields"]
        )

        self.assertIs(descriptor.field("attested_at").status, FieldStatus.UNKNOWN)
        self.assertIs(descriptor.field("attestation_verdict").status, FieldStatus.UNKNOWN)
        self.assertIs(
            descriptor.field("attestation_evidence_ref").status, FieldStatus.ABSENT
        )
        self.assertIs(
            descriptor.field("dispatch_lease_until").status, FieldStatus.UNAVAILABLE
        )
        # A JSON null is a governed value, not an absence.
        governed_null = descriptor.field("attestation_payload")
        self.assertIs(governed_null.status, FieldStatus.GOVERNED)
        self.assertIsNone(governed_null.value)

        self.assertEqual(
            descriptor.missing_required(), ("attestation_verdict", "attested_at")
        )
        self.assertTrue(
            all(
                descriptor.field(name).reason
                for name in descriptor.fields
                if descriptor.field(name).status is not FieldStatus.GOVERNED
            )
        )

        model = bridge.model_from_descriptor(descriptor)
        disposition = bridge.evaluate_required_fields(model)
        self.assertEqual(disposition["disposition"], "unevaluable")
        self.assertEqual(disposition["reason_code"], "unknown")
        self.assertEqual(len(disposition["missing"]), 2)

    def test_typed_status_cannot_smuggle_a_value(self):
        with self.assertRaises(ValueError):
            TypedValue(
                property_name="dispatch_state",
                status=FieldStatus.UNAVAILABLE,
                value_type="text",
                value="locally-promoted",
                reason="authority_backend_unavailable",
            )
        with self.assertRaises(ValueError):
            TypedValue(
                property_name="status",
                status=FieldStatus.UNKNOWN,
                value_type="text",
                value="invented",
            )


class TestLineage(unittest.TestCase):
    def test_authority_separation_and_dispatch_reconstruction(self):
        _, descriptor = descriptor_for(inst(WR_INSTANCE, "postgres_jsonb"))
        refs = {ref.relation: ref for ref in descriptor.lineage}
        self.assertEqual(refs["attestation"].authority, "postgres")
        self.assertFalse(refs["attestation"].reconstructable)
        self.assertEqual(refs["revision"].authority, "postgres")
        self.assertEqual(refs["dispatch"].authority, "mongo_reconstructable")
        self.assertTrue(refs["dispatch"].reconstructable)

        state = descriptor.authority
        self.assertEqual(state.mode, "authority_separated")
        self.assertEqual(state.attestation_authority, "postgres")
        self.assertEqual(state.lineage_authority, "postgres")
        self.assertEqual(state.dispatch_authority, "mongo_reconstructable")
        self.assertTrue(state.dispatch_reconstructable)
        self.assertFalse(state.marked_fallback)

    def test_lineage_survives_cross_materialization_round_trip(self):
        pg_instance, mongo_instance = pair(WR_INSTANCE)
        pg = adapter_for(pg_instance)
        pg_id = pg.write(pg_instance["document"])
        mongo = adapter_for(mongo_instance)
        mongo_id = mongo.write(mongo.encode(pg.read(pg_id)))
        self.assertEqual(
            pg.descriptor(pg_id).lineage,
            mongo.descriptor(mongo_id).lineage,
        )


class TestAuthorityBoundary(unittest.TestCase):
    def test_fail_closed_never_promotes_local_state(self):
        policy = AuthorityPolicy(postgres_available=False, mongo_available=True)
        instance = inst(WR_INSTANCE, "mongo_document")
        adapter = MockMongoAdapter(CATALOG, policy)
        result = evaluate_materialization(adapter, instance["document"])

        self.assertEqual(result.mode, "fail_closed")
        self.assertEqual(result.disposition, "refused")
        self.assertEqual(result.reason_code, "authority_backend_unavailable")
        self.assertIsNone(result.descriptor)
        self.assertIsNone(result.fingerprint)
        self.assertEqual(result.authority.attestation_authority, "none")
        self.assertEqual(result.authority.lineage_authority, "none")
        # The refused path must not even materialize a local copy.
        self.assertEqual(adapter.ids(), ())

    def test_degraded_dispatch_is_typed_unavailable_not_guessed(self):
        policy = AuthorityPolicy(postgres_available=True, mongo_available=False)
        instance = inst(WR_INSTANCE, "postgres_jsonb")
        adapter = MockJsonbAdapter(CATALOG, policy)
        object_id = adapter.write(instance["document"])
        descriptor = adapter.descriptor(object_id)

        self.assertEqual(descriptor.authority.mode, "degraded_reconstructable")
        self.assertEqual(descriptor.authority.dispatch_authority, "unavailable")
        self.assertTrue(descriptor.authority.dispatch_reconstructable)
        for name in DISPATCH_FIELDS:
            self.assertIs(descriptor.field(name).status, FieldStatus.UNAVAILABLE)
            self.assertIsNone(descriptor.field(name).value)
            self.assertEqual(descriptor.field(name).reason, "authority_backend_unavailable")

        result = evaluate_materialization(adapter_for(instance, policy), instance["document"])
        self.assertEqual(result.disposition, "asserted")
        self.assertEqual(result.mode, "degraded_reconstructable")

    def test_minimal_fallback_is_marked_with_declared_loss(self):
        policy = AuthorityPolicy(postgres_available=False, mongo_available=False)
        adapter = MockJsonbAdapter(CATALOG, policy)
        instance = inst(WR_INSTANCE, "postgres_jsonb")
        result = evaluate_materialization(adapter, instance["document"])

        self.assertEqual(result.mode, "minimal_fallback")
        self.assertEqual(result.disposition, "unevaluable")
        self.assertIsNotNone(result.descriptor)
        state = result.authority
        self.assertTrue(state.marked_fallback)
        self.assertEqual(state.durability, "process_only")
        self.assertEqual(
            set(state.authority_loss), {"attestation", "lineage", "dispatch"}
        )
        self.assertEqual(state.attestation_authority, "none")

        # The constrained-install descriptor is explicitly marked too.
        marked = minimal_fallback_descriptor(
            CATALOG,
            stereotype_name=WR,
            revision_id=3,
            values=inst(WR_INSTANCE, "postgres_jsonb")["document"]["values"],
        )
        self.assertEqual(marked.authority.mode, "minimal_fallback")
        self.assertTrue(marked.authority.marked_fallback)
        self.assertIsNotNone(semantic_fingerprint(marked))


class TestTypeDefinitionsHoldNoInstanceData(unittest.TestCase):
    def test_instance_data_never_enters_type_definitions(self):
        before = json.dumps(CATALOG.type_definitions(), sort_keys=True)
        self.assertNotIn("wr-8261656", before)
        self.assertNotIn("att-0001", before)
        self.assertNotIn("worker-63be8d6b", before)
        self.assertNotIn("doc-0001", before)

        # Identity/contract fingerprints are pure functions of type definitions.
        identity_before = CATALOG.identity(WR, 3)
        contract_before = CATALOG.effective_contract(WR, 3)
        for instance in INSTANCES.values():
            adapter_for(instance)  # normalize via a write/read cycle
            adapter = adapter_for(instance)
            adapter.descriptor(adapter.write(instance["document"]))
        self.assertEqual(CATALOG.identity(WR, 3), identity_before)
        self.assertEqual(CATALOG.effective_contract(WR, 3), contract_before)
        self.assertEqual(json.dumps(CATALOG.type_definitions(), sort_keys=True), before)

    def test_undeclared_instance_values_are_rejected(self):
        instance = json.loads(json.dumps(inst(WR_INSTANCE, "postgres_jsonb")))
        instance["document"]["values"]["shadow_store"] = {"untyped": True}
        adapter = adapter_for(instance)
        object_id = adapter.write(instance["document"])
        with self.assertRaises(ValueError) as caught:
            adapter.descriptor(object_id)
        self.assertIn("shadow semantic store", str(caught.exception))


class TestStagedInertArtifact(unittest.TestCase):
    def test_artifact_is_staged_inert_and_has_no_production_migration(self):
        self.assertTrue(core.STAGED_INERT)
        self.assertFalse(core.PRODUCTION_WIRING)
        self.assertTrue(bridge.STAGED_INERT)
        self.assertFalse(bridge.PRODUCTION_WIRING)
        self.assertEqual(core.CONFORMANCE_REVISION, CONFORMANCE_REVISION)

        self.assertTrue(PROOF_SQL.is_file())
        sql = PROOF_SQL.read_text()
        self.assertIn("STAGED-INERT", sql[:400])
        self.assertIn("CREATE TEMP TABLE", sql)
        self.assertIn("ROLLBACK", sql)
        self.assertNotIn(str(PROOF_SQL), str(list(MIGRATIONS.glob("*.sql"))))

        migration_names = {path.name for path in MIGRATIONS.glob("*.sql")}
        self.assertFalse(
            [name for name in migration_names if "conformance" in name or "storage" in name]
        )

        mirror = TS_MIRROR.read_text()
        self.assertIn("STAGED_INERT = true", mirror)
        self.assertIn("shrapnel:stereotype:", mirror)
        for status in ("governed", "unknown", "unavailable", "absent"):
            self.assertIn(f'"{status}"', mirror)

    def test_documentation_records_the_matrix(self):
        self.assertTrue(DOC.is_file())
        text = DOC.read_text()
        for heading in (
            "## 3. Acceptance matrix",
            "## 4. Typed value semantics",
            "## 5. Authority matrix",
            "## 6. Constrained-install boundary",
            "## 8. Non-goals / staged-inert declaration",
        ):
            self.assertIn(heading, text)
        self.assertIn("staged-inert", text)


if __name__ == "__main__":
    unittest.main()
