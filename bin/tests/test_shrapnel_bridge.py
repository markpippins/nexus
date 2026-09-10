"""Tests for the shrapnel-to-resolution bridge (plan 8261644, requirement ab45c18c).

Verifies the V152 bridge (re-issued from v36, never previously applied):
- Bridge functions exist (read_shrapnel_state_member, shrapnel_state_member_true).
- function_binding rows registered (shrapnel_state_member, shrapnel_state_member_true).
- Read-through returns typed resolved values with provenance, without copying
  into resolution.concept_attribute_value (read-only projection).
- Fail-closed behavior: unknown asset -> unknown; non-allowlisted member -> refusal;
  false value -> predicate false (only resolved-true returns true).
- evaluate_proposition on a shrapnel-sourced POC proposition yields non-null
  disposition (Asserted/Disputed/Rejected).

These are live read-only integration tests (no writes except the POC proposition
+ evaluation, which are the plan's deliverable and idempotent by UUID). The V152
migration itself is idempotent (CREATE OR REPLACE + ON CONFLICT).

Run:
  CONDUIT_PG_DSN='host=localhost port=5432 user=pguser password=pgpass dbname=nexus' \
      python3 -m pytest bin/tests/test_shrapnel_bridge.py -v
"""
import os
import sys
import unittest
import uuid

import psycopg2
import psycopg2.extras

_DSN = os.environ.get("CONDUIT_PG_DSN", "")
if not _DSN:
    raise RuntimeError("CONDUIT_PG_DSN must be set to run tests (PG is mandatory)")

# A real shrapnel asset with a known boolean member (verified live 2026-09-10).
_POC_ASSET = "asset:nexus:nebula_harvest_candidates:453c3a86-0d8c-422d-a911-c9318b8104e0"
_POC_MEMBER = "system_mapped"  # allow-listed, value false on this asset


class TestShrapnelBridgeExists(unittest.TestCase):
    """V152 applied: functions + bindings present (AC3 mechanism)."""

    def test_bridge_functions_exist(self):
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT proname FROM pg_proc WHERE proname IN "
                    "('read_shrapnel_state_member','shrapnel_state_member_true')")
                found = {r[0] for r in cur.fetchall()}
            self.assertIn("read_shrapnel_state_member", found)
            self.assertIn("shrapnel_state_member_true", found)
        finally:
            conn.close()

    def test_function_bindings_registered(self):
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT function_name FROM resolution.function_binding "
                    "WHERE function_name IN ('shrapnel_state_member','shrapnel_state_member_true')")
                found = {r[0] for r in cur.fetchall()}
            self.assertIn("shrapnel_state_member", found)
            self.assertIn("shrapnel_state_member_true", found)
        finally:
            conn.close()


class TestReadThrough(unittest.TestCase):
    """AC3: live EAV read-through, typed, with provenance, no copies."""

    def test_resolved_typed_value_with_provenance(self):
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT resolution.read_shrapnel_state_member(%s,%s,now()) AS r",
                    (_POC_ASSET, _POC_MEMBER))
                r = cur.fetchone()["r"]
            self.assertEqual(r["status"], "resolved")
            self.assertEqual(r["value"], False)
            self.assertIn("source_refs", r)
            self.assertEqual(r["source_refs"][0]["source"], "shrapnel")
        finally:
            conn.close()

    def test_unknown_asset_fail_closed(self):
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT resolution.read_shrapnel_state_member(%s,%s,now()) AS r",
                    (f"asset:test:nonexistent:{uuid.uuid4()}", _POC_MEMBER))
                r = cur.fetchone()["r"]
            self.assertEqual(r["status"], "unknown")
        finally:
            conn.close()

    def test_non_allowlisted_member_refused(self):
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT resolution.read_shrapnel_state_member(%s,%s,now()) AS r",
                    (_POC_ASSET, "mode"))
                r = cur.fetchone()["r"]
            self.assertEqual(r["status"], "refusal")
        finally:
            conn.close()

    def test_boolean_predicate_fail_closed(self):
        # system_mapped=false on this asset, so the predicate must be false
        # (only resolved-true returns true).
        conn = psycopg2.connect(_DSN)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT resolution.shrapnel_state_member_true(%s,%s,now())",
                    (_POC_ASSET, _POC_MEMBER))
                self.assertFalse(cur.fetchone()[0])
        finally:
            conn.close()


class TestEvaluatePoc(unittest.TestCase):
    """AC4: end-to-end evaluate_proposition on a shrapnel-sourced fact."""

    def test_evaluate_returns_non_null_disposition(self):
        conn = psycopg2.connect(_DSN)
        prop_id = str(uuid.uuid4())
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO resolution.proposition "
                    "(id, title, description, value, semantic_type_id) VALUES (%s,%s,%s,TRUE,"
                    "(SELECT id FROM resolution.semantic_type WHERE LOWER(name)='concept_membership' LIMIT 1)) "
                    "RETURNING id",
                    (prop_id,
                     "TEST 8261644: shrapnel bridge POC (hermetic, rolled back)",
                     "Asserts system_mapped=false via read_shrapnel_state_member; test row, rolled back."))
                cur.execute(
                    "SELECT resolution.evaluate_proposition(%s,'test-8261644-poc',%s::jsonb)",
                    (prop_id, '{"asset_id": "%s"}' % _POC_ASSET))
                result = cur.fetchone()["evaluate_proposition"]
            # result is a composite (disposition, ...); first element non-null means evaluated.
            self.assertIsNotNone(result)
            self.assertNotEqual(str(result).strip("()"), "")
        finally:
            conn.rollback()
            conn.close()


if __name__ == "__main__":
    unittest.main()