"""write_queue_reconciler contract tests — write-queue reconciliation.

Covers the pure classification/parsing logic of write_queue_reconciler.py
(no live NATS/DB required; DB interactions use a fake):

  - _unwrap handles both CanonicalEnvelope-wrapped and bare WriteQueueEntry
  - _classify detects non-replay outcomes:
      duplicate_submission, missing_capability, stale_version
  - clean entries classify as result (safe to apply)
  - _apply records to the applied table (idempotent on writeId)
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))

import write_queue_reconciler as wqr


class FakeDb:
    """Minimal fake psycopg2 connection: records _apply inserts."""

    def __init__(self):
        self.applied = []
        self.outbox = []
        self.updates = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class FakeCursor:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=None):
        if "INSERT INTO" in sql and "write_queue_applied" in sql:
            self.db.applied.append(params)
        if "INSERT INTO" in sql and "keychain_event_outbox" in sql:
            self.db.outbox.append(params)
        if "UPDATE" in sql and "write_queue_applied" in sql:
            self.db.updates.append(params)

    def fetchone(self):
        return ("0",)


class _Ctx:
    """Reset module state between tests (env capability, _seen)."""

    def __enter__(self):
        self.old_cap = os.environ.get("WRITE_QUEUE_CAPABILITY")
        os.environ["WRITE_QUEUE_CAPABILITY"] = "nexus.storage.canonical"
        wqr._seen.clear()
        return self

    def __exit__(self, *a):
        if self.old_cap is None:
            os.environ.pop("WRITE_QUEUE_CAPABILITY", None)
        else:
            os.environ["WRITE_QUEUE_CAPABILITY"] = self.old_cap
        wqr._seen.clear()


BASE_INTENT = {
    "writeId": "tr-1",
    "target": "solscript.proposition",
    "verb": "transition-entity",
    "payload": {"proposition_id": "p-1"},
    "schemaVersion": "1",
    "baseVersion": "0",
    "requiredCapability": "nexus.storage.canonical",
}


class TestUnwrap(unittest.TestCase):

    def test_bare_write_queue_entry(self):
        with _Ctx():
            entry, subj = wqr._unwrap({"intent": BASE_INTENT, "correlationId": "c-1"})
            self.assertEqual(entry["intent"]["writeId"], "tr-1")
            self.assertEqual(subj, "")

    def test_canonical_envelope_wrapped(self):
        with _Ctx():
            data = {
                "event_id": "evt-1", "event_type": "WriteQueueEntry",
                "subject": "nexus.write-queue.v1.solscript.proposition.transition-entity",
                "payload": {"intent": BASE_INTENT, "correlationId": "c-1"},
            }
            entry, subj = wqr._unwrap(data)
            self.assertEqual(entry["intent"]["writeId"], "tr-1")
            self.assertTrue(subj.startswith("nexus.write-queue"))

    def test_non_entry_ignored(self):
        with _Ctx():
            self.assertIsNone(wqr._unwrap({"event_type": "SomethingElse", "payload": {}}))


class TestClassify(unittest.TestCase):

    def test_clean_intent_is_result(self):
        with _Ctx():
            outcome, detail = wqr._classify(dict(BASE_INTENT), None, "tr-1")
            self.assertEqual(outcome, wqr.RESULT)

    def test_missing_capability(self):
        with _Ctx():
            intent = dict(BASE_INTENT)
            intent["requiredCapability"] = "some.other.capability"
            outcome, detail = wqr._classify(intent, None, "tr-2")
            self.assertEqual(outcome, wqr.MISSING_CAPABILITY)

    def test_duplicate_submission(self):
        with _Ctx():
            wqr._seen.add("tr-3")
            outcome, detail = wqr._classify(dict(BASE_INTENT), None, "tr-3")
            self.assertEqual(outcome, wqr.DUPLICATE_SUBMISSION)

    def test_stale_version(self):
        with _Ctx():
            # DB reports target version "5", intent based on "0" → stale
            db = FakeDb()
            db.fetchone_result = ("5",)  # not used; see below
            # Patch _current_target_version to return "5"
            orig = wqr._current_target_version
            wqr._current_target_version = lambda db, t: "5"
            try:
                outcome, detail = wqr._classify(dict(BASE_INTENT), db, "tr-4")
                self.assertEqual(outcome, wqr.STALE_VERSION)
            finally:
                wqr._current_target_version = orig


class TestApply(unittest.TestCase):

    def test_generic_intent_stages_and_applies(self):
        with _Ctx():
            db = FakeDb()
            # Generic target/verb → staging-only path (safe no-op apply)
            intent = dict(BASE_INTENT)
            intent["target"] = "some.other.surface"
            intent["verb"] = "upsert"
            ok, detail = wqr._apply(intent, db)
            self.assertTrue(ok)
            self.assertEqual(len(db.applied), 1, "staging row recorded")
            self.assertIn("staged", detail)
            # idempotent: re-apply is a no-op at the DB level (ON CONFLICT)
            ok2, _ = wqr._apply(intent, db)
            self.assertTrue(ok2)

    def test_transition_entity_requires_entity_and_transition(self):
        with _Ctx():
            db = FakeDb()
            # BASE_INTENT targets transition-entity but lacks entityId/transitionId
            ok, detail = wqr._apply(dict(BASE_INTENT), db)
            self.assertFalse(ok)
            self.assertIn("missing entityId/transitionId", detail)

    def test_transition_entity_applies_real_mutation(self):
        with _Ctx():
            db = FakeDb()
            intent = dict(BASE_INTENT)
            intent["writeId"] = "tr-e2e"
            intent["correlationId"] = "corr-e2e"
            intent["payload"] = {
                "entityId": "ent-1",
                "transitionId": "trans-1",
            }

            # Patch the interpreter to a committed outcome
            import types
            class _FakeInterp:
                last_transition_event = {"event_id": "evt-x"}
                def transition_entity(self, entity_id, transition_id, **kw):
                    return True, [{"rule_id": "r1", "passed": True}]

            import unittest.mock as um
            with um.patch("SOLScript.solscript.interpreter.ResolutionInterpreter",
                          return_value=_FakeInterp()):
                ok, detail = wqr._apply(intent, db)
            self.assertTrue(ok)
            self.assertIn("committed", detail)
            self.assertEqual(len(db.outbox), 1, "KeychainEvent recorded to outbox")
            # outbox outcome = committed
            self.assertEqual(db.outbox[0][3], "committed")
            # audit staging updated
            self.assertTrue(db.updates)


if __name__ == "__main__":
    unittest.main()