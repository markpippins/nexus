"""Unit tests for the append-only, hash-chained reconciliation log.

Plan 8261653. The envelope rule under test is *wrap-not-drop*: every
disposition is explicit, individually addressable, and append-only.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from nebula.duplicate_history import (  # noqa: E402
    ARCHIVE,
    MERGE,
    PRESERVE,
    build_inventory,
    fingerprint,
)
from nebula.reconciliation_log import (  # noqa: E402
    ReconciliationError,
    ReconciliationLog,
    dispositions_for,
)


class _LogCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "reconciliation.jsonl"
        self.log = ReconciliationLog(self.path)

    def append(self, **kw):
        base = dict(
            set_id="set-1",
            surface="vision.receipts",
            kind="duplicate",
            condition="C4",
            member_ref="rec-1",
            member_fingerprint="fp-1",
            disposition=PRESERVE,
            reason="primary of the set",
        )
        base.update(kw)
        return self.log.append(**base)


class AppendOnly(_LogCase):
    def test_first_append_starts_the_chain_at_genesis(self):
        e = self.append()
        self.assertEqual(e.seq, 0)
        self.assertEqual(e.prev_hash, "0" * 64)
        self.assertTrue(e.entry_hash)

    def test_entries_chain_to_their_predecessor(self):
        a = self.append()
        b = self.append(member_ref="rec-2", member_fingerprint="fp-2")
        self.assertEqual(b.seq, 1)
        self.assertEqual(b.prev_hash, a.entry_hash)

    def test_appending_does_not_truncate(self):
        self.append()
        self.append(member_ref="rec-2", member_fingerprint="fp-2")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_log_is_readable_as_jsonl(self):
        self.append()
        doc = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(doc["member_ref"], "rec-1")
        self.assertEqual(doc["disposition"], PRESERVE)

    def test_missing_file_is_an_empty_log(self):
        self.assertEqual(self.log.entries(), [])
        ok, problems = self.log.verify()
        self.assertTrue(ok)
        self.assertEqual(problems, [])


class Validation(_LogCase):
    def test_unknown_disposition_is_refused(self):
        with self.assertRaises(ReconciliationError):
            self.append(disposition="delete")

    def test_reason_is_required(self):
        with self.assertRaises(ReconciliationError):
            self.append(reason="   ")

    def test_member_ref_is_required(self):
        with self.assertRaises(ReconciliationError):
            self.append(member_ref="")

    def test_merge_requires_a_primary(self):
        with self.assertRaises(ReconciliationError):
            self.append(disposition=MERGE, primary_ref="")

    def test_merge_cannot_target_itself(self):
        with self.assertRaises(ReconciliationError):
            self.append(disposition=MERGE, member_ref="rec-9", primary_ref="rec-9")

    def test_archive_and_preserve_need_no_primary(self):
        self.append(disposition=ARCHIVE)
        self.append(member_ref="rec-2", member_fingerprint="fp-2", disposition=PRESERVE)


class ChainVerification(_LogCase):
    def test_valid_chain_verifies(self):
        self.append()
        self.append(member_ref="rec-2", member_fingerprint="fp-2")
        ok, problems = self.log.verify()
        self.assertTrue(ok, problems)

    def test_rewritten_entry_breaks_the_chain(self):
        self.append()
        self.append(member_ref="rec-2", member_fingerprint="fp-2")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        doc = json.loads(lines[0])
        doc["reason"] = "quietly rewritten"
        lines[0] = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, problems = self.log.verify()
        self.assertFalse(ok)
        self.assertTrue(problems)

    def test_removed_entry_breaks_the_chain(self):
        self.append()
        self.append(member_ref="rec-2", member_fingerprint="fp-2")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text(lines[1] + "\n", encoding="utf-8")
        ok, problems = self.log.verify()
        self.assertFalse(ok)


class SqlProjection(_LogCase):
    def test_renders_insert_statements(self):
        self.append()
        stmts = self.log.to_sql()
        self.assertTrue(any("reconciliation_disposition" in s for s in stmts))
        self.assertTrue(any("fingerprint_audit" in s for s in stmts))

    def test_escapes_quotes_in_reasons(self):
        self.append(reason="it's a 'quoted' reason")
        stmts = self.log.to_sql()
        self.assertTrue(any("it''s a ''quoted'' reason" in s for s in stmts))


class DispositionProposals(unittest.TestCase):
    def _inventory(self, **kw):
        return build_inventory(**kw)

    def test_content_identical_duplicate_is_a_merge(self):
        payload = {"k": 1}
        fp = fingerprint(payload)
        inv = self._inventory(receipts=[
            {"id": "r1", "payload_fingerprint": fp, "payload": payload,
             "created_at": "2026-01-01"},
            {"id": "r2", "payload_fingerprint": fp, "payload": payload,
             "created_at": "2026-01-02"},
        ])
        proposals = {(ref, d) for _, ref, d, _ in dispositions_for(inv)}
        self.assertIn(("r2", MERGE), proposals)
        self.assertIn(("r1", PRESERVE), proposals)

    def test_diverged_duplicate_is_preserved_not_merged(self):
        inv = self._inventory(manifests=[
            {"batch_id": "b1", "created_at": "2026-01-01",
             "candidates": [{"id": "a", "title": "one"}]},
            {"batch_id": "b2", "created_at": "2026-01-02",
             "candidates": [{"id": "a", "title": "two"}]},
        ])
        proposals = {(ref, d) for _, ref, d, _ in dispositions_for(inv)}
        self.assertIn(("b2", PRESERVE), proposals)

    def test_superseded_manifest_is_archived(self):
        inv = self._inventory(manifests=[
            {"batch_id": "b1", "created_at": "2026-01-01",
             "candidates": [{"id": "a", "title": "one"}]},
            {"batch_id": "b2", "created_at": "2026-01-02",
             "candidates": [{"id": "a", "title": "one"}],
             "superseded_by": "b1"},
        ])
        proposals = {(ref, d) for _, ref, d, _ in dispositions_for(inv)}
        self.assertIn(("b2", ARCHIVE), proposals)

    def test_every_proposal_carries_a_reason(self):
        inv = self._inventory(receipts=[
            {"id": "r1", "payload_fingerprint": "x" * 64, "payload": {"k": 1},
             "created_at": "2026-01-01"},
        ])
        for _, _, _, reason in dispositions_for(inv):
            self.assertTrue(reason.strip())


if __name__ == "__main__":
    unittest.main()
