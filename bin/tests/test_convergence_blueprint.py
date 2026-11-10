"""Guards for the plan 8261645 decomposition blueprint (schemas/convergence).

The blueprint is deliberately NON-AUTHORITATIVE: it decomposes convergence into
work packages and freezes contracts, but runs nothing and mutates no state. These
tests are hermetic (no DB, no network) and assert the properties that make that
claim true and inspectable:

  * every artifact declares plan 8261645 and its own identity/version;
  * the work-package registry covers all four tracks, and every package has an
    owner, dependencies, evidence outputs, a stop condition and a rollback;
  * declared unfilled evidence actually points at a real package (no dangling
    "we'll do it later" slots);
  * the interaction vocabulary is internally closed (every state transition
    target exists) and its ids are unique across the whole vocabulary;
  * the Brainfood contract's required fields are all contracted;
  * every Resolution JSONB column is classified into one of the five declared
    classes, and all five classes are used;
  * the KG projection freeze names an owner for each governed system and has
    not been seeded;
  * Track B does not claim a rehearsal it did not run.
"""

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas" / "convergence"
TYPESPEC = ROOT / "typespec" / "convergence"
PLAN = "8261645"

JSON_ARTIFACTS = [
    SCHEMAS / "work-packages.v1.json",
    SCHEMAS / "interaction-vocabulary.v1.json",
    SCHEMAS / "brainfood-slice.v1.json",
    SCHEMAS / "resolution-jsonb-classification.v1.json",
    TYPESPEC / "kg-projection-contract-freeze.v1.json",
]

TRACK_DOCS = [
    ROOT / "moleculer" / "convergence" / "track-a-moleculer-continuity.md",
    ROOT / "python" / "conduit" / "convergence" / "track-b-night-shift-rehearsal.md",
    ROOT / "python" / "aegis" / "convergence" / "track-c-aegis-wind-interaction-binding.md",
    ROOT / "python" / "vision" / "convergence" / "track-d-resolution-trigger-evidence.md",
]


def load(path):
    return json.loads(path.read_text())


class TestArtifactIdentity(unittest.TestCase):
    def test_every_artifact_parses_and_declares_the_plan(self):
        for path in JSON_ARTIFACTS:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), path)
                doc = load(path)
                self.assertEqual(doc.get("plan_ref"), PLAN, path.name)

    def test_frozen_contracts_carry_identity_version_and_freeze_time(self):
        # The four contracts are frozen artefacts. The work-package registry is
        # not: it is a versioned registry whose identity is its WorkRequest, so
        # it carries version + work_request_id instead of a freeze timestamp.
        for path in JSON_ARTIFACTS:
            if path.name == "work-packages.v1.json":
                continue
            with self.subTest(path=path.name):
                doc = load(path)
                identity = [
                    k for k in doc if k.endswith(("_id", "_version")) or k == "version"
                ]
                self.assertTrue(identity, f"{path.name} declares no id/version keys")
                self.assertIn("frozen_at", doc, path.name)

    def test_registry_is_versioned_and_bound_to_its_work_request(self):
        doc = load(SCHEMAS / "work-packages.v1.json")
        self.assertEqual(doc["version"], "1.0.0")
        self.assertTrue(doc["work_request_id"].startswith("wr-"))


class TestWorkPackages(unittest.TestCase):
    def setUp(self):
        self.doc = load(SCHEMAS / "work-packages.v1.json")
        self.packages = self.doc["packages"]

    def test_blueprint_is_non_authoritative(self):
        self.assertEqual(self.doc["blueprint_state"], "decomposition_complete")
        self.assertFalse(self.doc["runtime_claim"])
        self.assertFalse(self.doc["mutates_state"])

    def test_all_four_tracks_and_eleven_packages_are_registered(self):
        self.assertEqual(sorted(self.doc["tracks"]), ["A", "B", "C", "D"])
        ids = [p["id"] for p in self.packages]
        self.assertEqual(len(ids), 11, ids)
        self.assertEqual(len(set(ids)), len(ids), "duplicate package id")
        self.assertEqual({p["track"] for p in self.packages}, {"A", "B", "C", "D"})

    def test_every_package_has_owner_evidence_stop_and_rollback(self):
        for pkg in self.packages:
            with self.subTest(pkg=pkg["id"]):
                self.assertTrue(pkg.get("owner"), "no owner")
                self.assertIsInstance(pkg.get("dependencies"), list)
                self.assertTrue(pkg.get("evidence_outputs"), "no evidence outputs")
                self.assertTrue(pkg.get("stop_condition"), "no stop condition")
                self.assertTrue(pkg.get("rollback"), "no rollback")
                self.assertTrue(pkg.get("authority_boundary"), "no authority boundary")

    def test_package_dependencies_resolve(self):
        ids = {p["id"] for p in self.packages}
        for pkg in self.packages:
            for dep in pkg["dependencies"]:
                if re.fullmatch(r"[A-D]\d+", dep):
                    with self.subTest(pkg=pkg["id"], dep=dep):
                        self.assertIn(dep, ids)

    def test_unfilled_evidence_points_at_real_packages(self):
        slots = self.doc["unfilled_evidence"]
        self.assertTrue(slots, "a blueprint with no unfilled evidence would be overclaiming")
        ids = {p["id"] for p in self.packages}
        for slot in slots:
            with self.subTest(slot=slot):
                pkg_id = slot.split(".", 1)[0]
                self.assertIn(pkg_id, ids, f"unfilled evidence {slot} has no package")


class TestInteractionVocabulary(unittest.TestCase):
    def setUp(self):
        self.doc = load(SCHEMAS / "interaction-vocabulary.v1.json")

    def test_ids_are_unique_across_the_whole_vocabulary(self):
        seen = {}
        for section in ("actors", "roles", "states", "transitions", "rules", "invariants"):
            for entry in self.doc[section]:
                with self.subTest(section=section, id=entry["id"]):
                    self.assertNotIn(entry["id"], seen, f"{entry['id']} also in {seen.get(entry['id'])}")
                    seen[entry["id"]] = section

    def test_every_entry_declares_provenance(self):
        for section in ("actors", "roles", "states", "transitions", "rules", "invariants"):
            for entry in self.doc[section]:
                with self.subTest(section=section, id=entry["id"]):
                    prov = entry.get("provenance") or {}
                    self.assertTrue(prov.get("source"), "no provenance source")
                    self.assertIn(prov.get("kind"), {"declared", "derived", "observed"})

    def test_state_transition_targets_are_closed(self):
        known = {s["id"].rsplit(".", 1)[-1].lower() for s in self.doc["states"]}
        for state in self.doc["states"]:
            for target in state.get("transitions_to") or []:
                with self.subTest(state=state["id"], target=target):
                    self.assertIn(target.lower(), known)

    def test_declared_consumers_point_at_real_track_docs(self):
        for consumer in self.doc["consumers"]:
            with self.subTest(consumer=consumer["consumer"]):
                self.assertTrue((ROOT / consumer["artifact"]).is_file(), consumer["artifact"])


class TestBrainfoodSlice(unittest.TestCase):
    def setUp(self):
        self.doc = load(SCHEMAS / "brainfood-slice.v1.json")

    def test_required_fields_are_all_contracted(self):
        # `status` is contracted by status_enum rather than by field_contract;
        # every other required field must have an explicit field contract.
        enum_governed = {"status"} if self.doc.get("status_enum") else set()
        missing = [
            f for f in self.doc["required_fields"]
            if f not in self.doc["field_contract"] and f not in enum_governed
        ]
        self.assertEqual(missing, [])

    def test_status_enum_is_explicit_and_unique(self):
        values = [s["value"] for s in self.doc["status_enum"]]
        self.assertEqual(len(values), len(set(values)))
        self.assertIn("recorded", values)
        self.assertIn("superseded", values)


class TestResolutionJsonbClassification(unittest.TestCase):
    def setUp(self):
        self.doc = load(SCHEMAS / "resolution-jsonb-classification.v1.json")

    def test_all_five_classes_are_declared(self):
        self.assertEqual(
            set(self.doc["classes"]),
            {"payload_only", "queryable_identity", "lineage", "authority", "projection"},
        )

    def test_every_column_is_classified_into_a_declared_class(self):
        classes = set(self.doc["classes"])
        self.assertTrue(self.doc["columns"])
        for column in self.doc["columns"]:
            with self.subTest(table=column["table"], column=column["column"]):
                self.assertIn(column["class"], classes)
                self.assertTrue(column.get("rationale"))

    def test_every_class_is_actually_used(self):
        used = {c["class"] for c in self.doc["columns"]}
        self.assertEqual(used, set(self.doc["classes"]))


class TestKgProjectionFreeze(unittest.TestCase):
    def setUp(self):
        self.doc = load(TYPESPEC / "kg-projection-contract-freeze.v1.json")

    def test_freeze_is_not_seeded(self):
        self.assertEqual(self.doc["state"], "frozen_pending_seed")
        self.assertFalse(self.doc["seeding_performed"])

    def test_each_governed_system_has_an_owner(self):
        ownership = self.doc["ownership"]
        for system in ("resolution", "shrapnel", "aegis", "semantics", "keychains"):
            with self.subTest(system=system):
                self.assertIn(system, ownership)
                entry = ownership[system]
                self.assertTrue(entry.get("owns"), f"{system} owns nothing")
                self.assertTrue(entry.get("contract"), f"{system} names no contract")

    def test_sections_relations_and_provenance_are_frozen(self):
        self.assertTrue(self.doc["sections"])
        self.assertTrue(self.doc["relations"])
        self.assertTrue(self.doc["provenance"])
        self.assertTrue(self.doc["seed_gate"].get("checks"))
        self.assertTrue(self.doc["stop_condition"])


class TestTrackDocs(unittest.TestCase):
    def test_each_track_doc_exists_and_names_the_plan(self):
        for path in TRACK_DOCS:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), path)
                self.assertIn(PLAN, path.read_text())

    def test_track_b_does_not_claim_a_rehearsal_it_did_not_run(self):
        text = (ROOT / "python" / "conduit" / "convergence"
                / "track-b-night-shift-rehearsal.md").read_text()
        self.assertIn("rehearsal has not been run", text)
        # The blueprint must keep its evidence slots visibly unfilled rather than
        # asserting a transcript/receipt chain that no run produced.
        self.assertIn("unfilled", text.lower())


if __name__ == "__main__":
    unittest.main()
