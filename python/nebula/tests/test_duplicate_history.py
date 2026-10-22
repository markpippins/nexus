"""Unit tests for the W-A duplicate-history detectors (plan 8261653).

The detectors are pure functions over plain structures, so these tests need
no database. The end-to-end shape of the migration is covered separately by
``test_v205_w_a_reconciliation_e2e.py``.
"""

import json
import os
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from nebula.duplicate_history import (  # noqa: E402
    ARCHIVE,
    C1,
    C2,
    C3,
    C4,
    KIND_DUPLICATE,
    KIND_UNCOVERED,
    KIND_UNIDENTIFIED,
    KIND_UNVERIFIED,
    MERGE,
    PC7,
    PRESERVE,
    ST02,
    build_inventory,
    canonical_asset_sets,
    candidate_set_identity,
    claim_sets,
    disposition_audit_sets,
    fingerprint,
    legacy_coverage_sets,
    manifest_sets,
    outbox_sets,
    receipt_sets,
)


def _candidate(cid, title="t", readiness="ready"):
    return {"id": cid, "title": title, "readiness": readiness}


class CandidateSetIdentity(unittest.TestCase):
    def test_order_independent(self):
        a = [_candidate("a"), _candidate("b")]
        b = [_candidate("b"), _candidate("a")]
        self.assertEqual(candidate_set_identity(a), candidate_set_identity(b))

    def test_duplicate_tolerant(self):
        """R1.3: the canonical set key collapses duplicates within a set."""
        a = [_candidate("a"), _candidate("b")]
        b = [_candidate("a"), _candidate("b"), _candidate("a")]
        self.assertEqual(candidate_set_identity(a), candidate_set_identity(b))

    def test_distinct_sets_differ(self):
        a = [_candidate("a")]
        b = [_candidate("b")]
        self.assertNotEqual(candidate_set_identity(a), candidate_set_identity(b))

    def test_matches_production_implementation(self):
        """The inventory must reproduce ST.02's grouping exactly.

        ``python/promotion-flow/promotion_common.py`` is the production
        implementation of the same identity. It is not importable as a
        package (the directory name has a dash), so it is loaded by path.
        The two implementations differ in one respect — this one collapses
        duplicate ids, the production one does not — and the real corpus
        contains no set with a repeated id, so they must agree on it.
        """
        path = _REPO_ROOT / "python" / "promotion-flow" / "promotion_common.py"
        if not path.exists():
            self.skipTest("promotion_common.py not present")
        import importlib.util

        spec = importlib.util.spec_from_file_location("_promotion_common", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        prod = mod.candidate_set_identity

        corpus = sorted((_REPO_ROOT / "state" / "promotion-flow").glob("batch-*.json"))
        if not corpus:
            self.skipTest("no promotion manifests on disk")
        for f in corpus:
            doc = json.loads(f.read_text(encoding="utf-8"))
            cands = doc.get("candidates", [])
            ids = [str(c["id"]) for c in cands if c.get("id")]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate ids in {f.name}")
            self.assertEqual(candidate_set_identity(cands), prod(cands), f.name)


class ManifestDetector(unittest.TestCase):
    def test_groups_by_identity_and_picks_earliest_primary(self):
        manifests = [
            {
                "batch_id": "b-late",
                "created_at": "2026-08-27T00:00:00Z",
                "candidates": [_candidate("a"), _candidate("b")],
            },
            {
                "batch_id": "b-early",
                "created_at": "2026-08-26T00:00:00Z",
                "candidates": [_candidate("b"), _candidate("a")],
            },
        ]
        sets = manifest_sets(manifests)
        self.assertEqual(len(sets), 1)
        s = sets[0]
        self.assertEqual(s.surface, "promotion-manifest")
        self.assertEqual(s.kind, KIND_DUPLICATE)
        self.assertEqual(s.condition, ST02)
        self.assertEqual(len(s.members), 2)
        self.assertEqual(s.members[0].ref, "b-early")  # earliest is primary
        self.assertEqual(s.members[0].proposed_disposition, PRESERVE)
        self.assertEqual(s.members[1].proposed_disposition, MERGE)

    def test_already_superseded_member_is_archived(self):
        manifests = [
            {
                "batch_id": "b1",
                "created_at": "2026-08-26T00:00:00Z",
                "candidates": [_candidate("a")],
            },
            {
                "batch_id": "b2",
                "created_at": "2026-08-27T00:00:00Z",
                "candidates": [_candidate("a")],
                "superseded_by": "b1",
                "supersession_reason": "duplicate candidate-set emission (ST.02)",
            },
        ]
        s = manifest_sets(manifests)[0]
        self.assertEqual(s.members[1].disposition, "archive")
        self.assertEqual(s.members[1].proposed_disposition, ARCHIVE)
        # The primary still needs an explicit preserve in the ledger, so the
        # set is not yet fully dispositioned (mirrors V205's status rule).
        self.assertFalse(s.dispositioned)

    def test_singleton_set_is_not_a_duplicate(self):
        manifests = [{"batch_id": "b1", "created_at": "x", "candidates": [_candidate("a")]}]
        self.assertEqual(manifest_sets(manifests), [])


class ReceiptDetector(unittest.TestCase):
    def test_groups_by_payload_fingerprint(self):
        payload = {"k": 1}
        fp = fingerprint(payload)
        receipts = [
            {"id": "r1", "payload_fingerprint": fp, "payload": payload,
             "created_at": "2026-01-01"},
            {"id": "r2", "payload_fingerprint": fp, "payload": payload,
             "created_at": "2026-01-02"},
        ]
        sets = receipt_sets(receipts)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].condition, C1)
        self.assertEqual(len(sets[0].members), 2)

    def test_mismatched_fingerprint_is_unverified_not_silently_fixed(self):
        receipts = [
            {"id": "r1", "payload_fingerprint": "deadbeef" * 8,
             "payload": {"k": 1}, "created_at": "2026-01-01"},
        ]
        sets = receipt_sets(receipts)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].kind, KIND_UNVERIFIED)
        self.assertIn("does not match", sets[0].members[0].detail)

    def test_missing_fingerprint_is_unverified(self):
        receipts = [{"id": "r1", "payload_fingerprint": None,
                     "payload": {"k": 1}, "created_at": "2026-01-01"}]
        sets = receipt_sets(receipts)
        self.assertEqual(sets[0].kind, KIND_UNVERIFIED)
        self.assertIn("no fingerprint", sets[0].members[0].detail)

    def test_unique_fingerprints_produce_no_set(self):
        receipts = [
            {"id": "r1", "payload_fingerprint": fingerprint({"k": 1}),
             "payload": {"k": 1}, "created_at": "2026-01-01"},
        ]
        self.assertEqual(receipt_sets(receipts), [])


class CanonicalAssetDetector(unittest.TestCase):
    def test_null_key_makes_the_set_ungroupable(self):
        assets = [
            {"id": "a1", "canonical_key": None, "created_at": "2026-01-01"},
            {"id": "a2", "canonical_key": None, "created_at": "2026-01-02"},
        ]
        sets = canonical_asset_sets(assets)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].kind, KIND_UNIDENTIFIED)
        self.assertEqual(sets[0].condition, C2)
        self.assertEqual(len(sets[0].members), 2)

    def test_shared_key_is_a_duplicate_set(self):
        assets = [
            {"id": "a1", "canonical_key": "k1", "created_at": "2026-01-01"},
            {"id": "a2", "canonical_key": "k1", "created_at": "2026-01-02"},
        ]
        sets = canonical_asset_sets(assets)
        self.assertEqual(sets[0].kind, KIND_DUPLICATE)

    def test_jsonb_canonical_key_is_grouped_canonically(self):
        """Live canonical_key is JSONB: key order must not split a set."""
        assets = [
            {"id": "a1", "canonical_key": {"b": 1, "a": 2},
             "created_at": "2026-01-01"},
            {"id": "a2", "canonical_key": {"a": 2, "b": 1},
             "created_at": "2026-01-02"},
        ]
        sets = canonical_asset_sets(assets)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].kind, KIND_DUPLICATE)
        self.assertEqual(len(sets[0].members), 2)

    def test_empty_jsonb_key_is_unidentified(self):
        sets = canonical_asset_sets([
            {"id": "a1", "canonical_key": {}, "created_at": "2026-01-01"},
        ])
        self.assertEqual(sets[0].kind, KIND_UNIDENTIFIED)


class ClaimDetector(unittest.TestCase):
    def test_divergence_between_jsonb_and_column_is_flagged(self):
        claims = [
            {"id": "c1", "attempt_id": "att-1",
             "subject_ref": {"attempt_id": "att-9"}, "created_at": "2026-01-01"},
        ]
        sets = claim_sets(claims)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].kind, KIND_UNVERIFIED)
        self.assertEqual(sets[0].condition, C3)
        self.assertIn("disagree", sets[0].members[0].detail)

    def test_empty_subject_ref_is_flagged(self):
        claims = [
            {"id": "c1", "attempt_id": None, "subject_ref": {},
             "created_at": "2026-01-01"},
        ]
        sets = claim_sets(claims)
        self.assertEqual(sets[0].kind, KIND_UNVERIFIED)
        self.assertIn("no attempt_id", sets[0].members[0].detail)

    def test_agreeing_claim_is_clean(self):
        claims = [
            {"id": "c1", "attempt_id": "att-1",
             "subject_ref": {"attempt_id": "att-1"}, "created_at": "2026-01-01"},
        ]
        self.assertEqual(claim_sets(claims), [])


class OutboxDetector(unittest.TestCase):
    def test_groups_by_read_set(self):
        rows = [
            {"id": "o1", "read_set": {"a": 1}, "created_at": "2026-01-01"},
            {"id": "o2", "read_set": {"a": 1}, "created_at": "2026-01-02"},
        ]
        sets = outbox_sets(rows)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].condition, PC7)

    def test_empty_read_set_is_skipped(self):
        rows = [{"id": "o1", "read_set": {}, "created_at": "2026-01-01"}]
        self.assertEqual(outbox_sets(rows), [])

    def test_member_dates_come_from_their_own_row(self):
        """Regression: the member loop must not leak the outer row variable."""
        rows = [
            {"id": "o1", "read_set": {"a": 1}, "created_at": "2026-01-01"},
            {"id": "o2", "read_set": {"a": 1}, "created_at": "2026-01-02"},
        ]
        s = outbox_sets(rows)[0]
        self.assertEqual(
            [m.created_at for m in s.members], ["2026-01-01", "2026-01-02"]
        )


class LegacyCoverageDetector(unittest.TestCase):
    def test_finds_rows_without_a_disposition(self):
        legacy = [
            {"id": "r1", "status": "completed", "created_at": "2026-01-01"},
            {"id": "r2", "status": "expired", "created_at": "2026-01-02"},
        ]
        dispositions = [{"source_table": "receipts", "source_pk": "r0",
                         "migration_version": "C4"}]
        sets = legacy_coverage_sets(
            legacy, dispositions, source_table="receipts",
            migration_version="C4", surface="vision.receipts")
        # grouped by status -> two cohorts
        self.assertEqual(len(sets), 2)
        self.assertTrue(all(s.kind == KIND_UNCOVERED for s in sets))
        self.assertTrue(all(s.condition == C4 for s in sets))

    def test_covered_row_produces_no_set(self):
        legacy = [{"id": "r1", "status": "completed"}]
        dispositions = [{"source_table": "receipts", "source_pk": "r1",
                         "migration_version": "C4"}]
        sets = legacy_coverage_sets(
            legacy, dispositions, source_table="receipts",
            migration_version="C4", surface="vision.receipts")
        self.assertEqual(sets, [])

    def test_other_lane_does_not_count_as_covered(self):
        """C6 dispositions do not cover a C4 receipt."""
        legacy = [{"id": "r1", "status": "completed"}]
        dispositions = [{"source_table": "receipts", "source_pk": "r1",
                         "migration_version": "C6"}]
        sets = legacy_coverage_sets(
            legacy, dispositions, source_table="receipts",
            migration_version="C4", surface="vision.receipts")
        self.assertEqual(len(sets), 1)


class DispositionAuditDetector(unittest.TestCase):
    def test_groups_sharing_identical_target_refs(self):
        dispositions = [
            {"id": "d1", "source_pk": "r1", "target_refs": {"x": 1},
             "created_at": "2026-01-01"},
            {"id": "d2", "source_pk": "r2", "target_refs": {"x": 1},
             "created_at": "2026-01-02"},
        ]
        sets = disposition_audit_sets(dispositions)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].kind, KIND_UNVERIFIED)
        self.assertIn("no per-row fingerprint", sets[0].detail)

    def test_distinct_target_refs_produce_no_set(self):
        dispositions = [
            {"id": "d1", "source_pk": "r1", "target_refs": {"x": 1}},
            {"id": "d2", "source_pk": "r2", "target_refs": {"x": 2}},
        ]
        self.assertEqual(disposition_audit_sets(dispositions), [])


class InventoryAssembly(unittest.TestCase):
    def test_set_ids_are_deterministic_and_order_stable(self):
        kwargs = dict(
            manifests=[
                {"batch_id": "b2", "created_at": "2026-01-02",
                 "candidates": [_candidate("a")]},
                {"batch_id": "b1", "created_at": "2026-01-01",
                 "candidates": [_candidate("a")]},
            ],
            receipts=[
                {"id": "r1", "payload_fingerprint": fingerprint({"k": 1}),
                 "payload": {"k": 1}, "created_at": "2026-01-01"},
            ],
        )
        first = build_inventory(**kwargs)
        second = build_inventory(**kwargs)
        self.assertEqual([s.id for s in first.sets], [s.id for s in second.sets])
        self.assertEqual(
            json.dumps(first.to_dict(), sort_keys=True),
            json.dumps(second.to_dict(), sort_keys=True),
        )

    def test_every_set_has_a_package_id(self):
        inv = build_inventory(
            receipts=[{"id": "r1", "payload_fingerprint": "x" * 64,
                       "payload": {"k": 1}, "created_at": "2026-01-01"}]
        )
        for s in inv.sets:
            self.assertTrue(s.package_id.startswith("W-A/"))

    def test_open_packages_excludes_fully_dispositioned_sets(self):
        from nebula.duplicate_history import DuplicateSet, Inventory, Member

        s = DuplicateSet(
            surface="promotion-manifest",
            kind=KIND_DUPLICATE,
            condition=ST02,
            identity_kind="candidate_set_identity",
            identity_value="x",
            members=(
                Member(ref="b1", fingerprint="f1", disposition=PRESERVE),
                Member(ref="b2", fingerprint="f1", disposition=ARCHIVE),
            ),
        )
        inv = Inventory(sets=(s,))
        self.assertTrue(s.dispositioned)
        self.assertEqual(len(inv.open_packages), 0)

    def test_set_with_no_recorded_disposition_stays_open(self):
        """A ``superseded_by`` marker is evidence, not a recorded disposition.

        The surviving primary still needs an explicit ``preserve`` in the
        ledger, exactly as V205's member-count status rule requires.
        """
        inv = build_inventory(
            manifests=[
                {"batch_id": "b1", "created_at": "2026-01-01",
                 "candidates": [_candidate("a")]},
                {"batch_id": "b2", "created_at": "2026-01-02",
                 "candidates": [_candidate("a")],
                 "superseded_by": "b1"},
            ]
        )
        self.assertEqual(len(inv.sets), 1)
        self.assertFalse(inv.sets[0].dispositioned)
        self.assertEqual(len(inv.open_packages), 1)

    def test_single_member_unverified_set_is_open(self):
        """A lone unverified record must be dispositioned, not assumed settled."""
        inv = build_inventory(
            receipts=[{"id": "r1", "payload_fingerprint": "deadbeef" * 8,
                       "payload": {"k": 1}, "created_at": "2026-01-01"}]
        )
        self.assertEqual(len(inv.sets), 1)
        self.assertEqual(len(inv.open_packages), 1)


if __name__ == "__main__":
    unittest.main()
