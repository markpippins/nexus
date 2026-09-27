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
        self.last_sql = ""
        # Pre-existence verdict for the reconciler's to_regclass assert
        # (True = canonical DDL provisioned the staging table).
        self.applied_table_exists = True

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
        self.db.last_sql = sql
        if "INSERT INTO" in sql and "write_queue_applied" in sql:
            self.db.applied.append(params)
        if "INSERT INTO" in sql and "keychain_event_outbox" in sql:
            self.db.outbox.append(params)
        if "UPDATE" in sql and "write_queue_applied" in sql:
            self.db.updates.append(params)

    def fetchone(self):
        # The staging pre-existence assert (to_regclass) is the only SELECT
        # the staging path issues; everything else gets the legacy ("0",).
        if "to_regclass" in self.db.last_sql:
            return ("t",) if self.db.applied_table_exists else (None,)
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


class TestHydration(unittest.TestCase):
    """Governed-transition interpreter hydration (startup seed loading).

    The reconciler hydrates once via hydrate_interpreter(); with no usable
    governed surfaces (entities + state transitions) it stays unhydrated and
    _apply_transition_entity decides on a fresh empty interpreter — the
    deterministic entity-not-found rejection, durably recorded.
    """

    def _reset(self):
        wqr._HYDRATED_INTERPRETER = None
        wqr._HYDRATION_ATTEMPTED = False

    def setUp(self):
        self._reset()

    def tearDown(self):
        self._reset()

    def test_get_interpreter_hydrated_wins(self):
        sentinel = object()
        wqr._HYDRATED_INTERPRETER = sentinel
        self.assertIs(wqr._get_interpreter(), sentinel)

    def test_get_interpreter_unhydrated_is_fresh(self):
        a = wqr._get_interpreter()
        b = wqr._get_interpreter()
        self.assertIsNot(a, b, "unhydrated path must hand out fresh empty interpreters")

    def test_hydrate_without_dsn_is_false(self):
        import asyncio
        self.assertFalse(asyncio.run(wqr.hydrate_interpreter(None)))
        self.assertTrue(wqr._HYDRATION_ATTEMPTED)
        self.assertIsNone(wqr._HYDRATED_INTERPRETER)

    def _patch_asyncpg(self, pool):
        import types, unittest.mock as um
        fake = types.SimpleNamespace()
        async def _create_pool(*a, **kw):
            return pool
        fake.create_pool = _create_pool
        return um.patch.dict("sys.modules", {"asyncpg": fake})

    def test_hydrate_empty_store_stays_unhydrated(self):
        """Schema-only store (no entities/transitions) → unhydrated verdict."""
        import asyncio, types, unittest.mock as um

        class _FakePool:
            async def close(self):
                pass

        class _FakeLoader:
            def __init__(self, interp, pool):
                self.interp = interp
            async def load_all(self):
                pass  # store carries no seed rows

        with self._patch_asyncpg(_FakePool()), \
                um.patch("SOLScript.solscript.database_loader.DatabaseLoader", _FakeLoader):
            ok = asyncio.run(wqr.hydrate_interpreter("postgres://x"))
        self.assertFalse(ok, "empty store must not hydrate")
        self.assertIsNone(wqr._HYDRATED_INTERPRETER)

    def test_hydrate_with_seed_data_enables_governed_path(self):
        """Entities + transitions present → hydrated; _apply uses it."""
        import asyncio, types, unittest.mock as um

        class _FakePool:
            async def close(self):
                pass

        class _FakeLoader:
            def __init__(self, interp, pool):
                self.interp = interp
            async def load_all(self):
                self.interp.entities["e-1"] = object()
                self.interp.state_transitions["t-1"] = object()

        with self._patch_asyncpg(_FakePool()), \
                um.patch("SOLScript.solscript.database_loader.DatabaseLoader", _FakeLoader):
            ok = asyncio.run(wqr.hydrate_interpreter("postgres://x"))
        self.assertTrue(ok)
        self.assertIsNotNone(wqr._HYDRATED_INTERPRETER)

        # The governed apply path must decide against the HYDRATED instance.
        class _HydratedInterp:
            last_transition_event = {"event_id": "evt-h"}
            def transition_entity(self, entity_id, transition_id, **kw):
                return True, [{"rule_id": "r1", "passed": True}]

        wqr._HYDRATED_INTERPRETER = _HydratedInterp()
        db = FakeDb()
        intent = dict(BASE_INTENT)
        intent["writeId"] = "tr-hyd"
        intent["correlationId"] = "corr-hyd"
        intent["payload"] = {"entityId": "e-1", "transitionId": "t-1"}
        ok, detail = wqr._apply(intent, db)
        self.assertTrue(ok)
        self.assertIn("committed", detail)
        self.assertEqual(len(db.outbox), 1, "KeychainEvent to outbox")

    def test_governed_refusal_is_a_result_not_partial_application(self):
        """Contract (typespec/v1/write-queue): a DECIDED refusal finished the
        reconciliation — outcome result — it is NOT partial_application
        ("only part of the mutation applied"). Empirically misclassified on
        the first live governed arc, 2026-09-25.
        """
        import unittest.mock as um

        class _RefusingInterp:
            last_transition_event = {"event_id": "evt-r"}
            def transition_entity(self, entity_id, transition_id, **kw):
                return False, [{"rule_id": "r1", "passed": False}]

        with _Ctx(), um.patch("SOLScript.solscript.interpreter.ResolutionInterpreter",
                              return_value=_RefusingInterp()):
            db = FakeDb()
            intent = dict(BASE_INTENT)
            intent["writeId"] = "tr-refuse"
            intent["payload"] = {"entityId": "e-1", "transitionId": "t-1"}
            ok, detail = wqr._apply(intent, db)
        self.assertTrue(ok, "refused is a decided, completed reconciliation")
        self.assertIn("refused", detail)
        self.assertEqual(len(db.outbox), 1, "refusal KeychainEvent durably recorded")
        self.assertEqual(db.outbox[0][3], "refused")


class TestStagingTableAssert(unittest.TestCase):
    """write_queue_applied pre-existence assert (tester inspection cbe83e25).

    The staging table is canonical DDL (nexus-ci-bootstrap.sql snapshot +
    production V-migrations). The reconciler used to self-provision with
    CREATE TABLE IF NOT EXISTS — masking snapshot drift behind an inferred
    local shape. It now asserts pre-existence and fails loudly.
    """

    def test_missing_table_fails_loudly(self):
        with _Ctx():
            db = FakeDb()
            db.applied_table_exists = False
            intent = dict(BASE_INTENT)
            intent["writeId"] = "tr-missing"
            ok, detail = wqr._apply(intent, db)
        self.assertFalse(ok, "absent canonical surface must refuse to apply")
        self.assertIn("write_queue_applied", detail)
        self.assertIn("self-provision", detail)
        self.assertEqual(db.applied, [], "no staging insert when the surface is absent")

    def test_present_table_stages_normally(self):
        with _Ctx():
            db = FakeDb()
            intent = dict(BASE_INTENT)
            intent["writeId"] = "tr-present"
            # Unmapped target: isolates the staging assert from the
            # governed interpreter path (covered by its own tests).
            intent["target"] = "ci.probe/touch"
            intent["verb"] = "touch"
            ok, detail = wqr._apply(intent, db)
        self.assertTrue(ok)
        self.assertEqual(len(db.applied), 1, "staging row recorded")

    def test_staging_never_creates_tables(self):
        """The CREATE TABLE IF NOT EXISTS drift-mask must stay gone."""
        import inspect
        src = inspect.getsource(wqr._stage_applied)
        self.assertNotIn("CREATE TABLE", src)


if __name__ == "__main__":
    unittest.main()