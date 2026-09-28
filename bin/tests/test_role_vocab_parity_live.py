#!/usr/bin/env python3
"""R5 — live cross-schema role-vocabulary parity (Decision 5, thread 62ca3c1c).

Decision 5 (PR #610 / migration 054): "A comment is a description; a test
is a guarantee." R3 in test_role_vocab_parity_runner.py checks claim-vs-DDL
consistency (does the code match its description?); THIS module checks that
the described invariant is actually TRUE:

    nebula.agent_records_history and scratch.agent_records_history must
    accept the same role vocabulary, verified mechanically by comparing
    pg_get_constraintdef of agent_records_role_check on both schemas.

Verdict rules (Decision 5 item 4):
  - both schemas present and definitions equal          -> PASS
  - both schemas present and definitions differ         -> FAIL (diff shown)
  - nebula present, scratch ABSENT                      -> FAIL (the
    Decision-3/5 asymmetry class is a defect, not a skip)
  - nothing reachable (no PG / hermetic context)        -> EXPLICIT SKIP
    (never silent: unittest reports the skip with its reason)

Naming note: the runner battery already uses R4 for its canonical-tables
guard, so Decision 5's proposed parity rule lands here as R5.

Run:
  python3 -m pytest bin/tests/test_role_vocab_parity_live.py -v

Connection (env-overridable, defaults match the throwaway/local stack):
  PGIE_TEST_PGHOST (localhost) PGIE_TEST_PGPORT (5432)
  PGIE_TEST_PGUSER (pguser)    PGIE_TEST_PGPASSWORD (pgpass)
  PGIE_TEST_PGDATABASE (nexus)
Password is passed via PGPASSWORD env only — never a prompting TTY.
"""
import os
import subprocess
import unittest

PROBE_TIMEOUT = 15

CONSTRAINT_SQL = """
SELECT to_regclass('nebula.agent_records_history')::text,
       to_regclass('scratch.agent_records_history')::text,
       COALESCE((SELECT pg_get_constraintdef(c.oid)
                   FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = 'nebula' AND t.relname = 'agent_records_history'
                    AND c.conname = 'agent_records_role_check'), '') AS nebula_def,
       COALESCE((SELECT pg_get_constraintdef(c.oid)
                   FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = 'scratch' AND t.relname = 'agent_records_history'
                    AND c.conname = 'agent_records_role_check'), '') AS scratch_def;
"""


def psql_env():
    return {
        **os.environ,
        "PGPASSWORD": os.environ.get("PGIE_TEST_PGPASSWORD", "pgpass"),
        "PGHOST": os.environ.get("PGIE_TEST_PGHOST", "localhost"),
        "PGPORT": os.environ.get("PGIE_TEST_PGPORT", "5432"),
        "PGUSER": os.environ.get("PGIE_TEST_PGUSER", "pguser"),
        "PGDATABASE": os.environ.get("PGIE_TEST_PGDATABASE", "nexus"),
    }


def probe_constraint_state():
    """One psql round-trip -> dict, or None when the DB is unreachable."""
    try:
        proc = subprocess.run(
            ["psql", "-X", "-A", "-t", "-F", "\x1f", "-v", "ON_ERROR_STOP=1",
             "-c", CONSTRAINT_SQL],
            env=psql_env(), capture_output=True, text=True,
            timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    parts = proc.stdout.strip().split("\x1f")
    if len(parts) != 4:
        return None
    nebula_rel, scratch_rel, nebula_def, scratch_def = (
        p.strip() for p in parts)
    return {
        "nebula_rel": nebula_rel,
        "scratch_rel": scratch_rel,
        "nebula_def": nebula_def,
        "scratch_def": scratch_def,
    }


def parity_verdict(nebula_def, scratch_def):
    """Pure core: (verdict, message) from the two constraint definitions.

    verdict is "pass", "fail", or "missing" (scratch mirror absent while
    nebula carries the CHECK — the asymmetry defect, never a skip).
    """
    if not nebula_def:
        return ("fail",
                "R5 GUARD: nebula.agent_records_role_check not found — the "
                "source of truth itself is missing (probe was healthy)")
    if not scratch_def:
        return ("missing",
                "R5: scratch.agent_records_history carries NO "
                "agent_records_role_check while nebula does — cross-schema "
                "asymmetry (Decision 3/5 defect class), failing not skipping")
    if nebula_def == scratch_def:
        return ("pass",
                "R5: role-vocabulary parity holds (nebula == scratch, "
                "byte-equal pg_get_constraintdef)")
    return ("fail",
            "R5 DIVERGENCE: nebula and scratch accept different role "
            "vocabularies — the described invariant is false.\n"
            f"  nebula:   {nebula_def}\n"
            f"  scratch:  {scratch_def}")


class ParityVerdictUnitTest(unittest.TestCase):
    """Hermetic: the verdict core on synthetic definitions (no DB)."""

    N24 = ("CHECK (((role)::text = ''::text OR (role)::text = ANY "
           "((ARRAY['architect'::character varying, 24 tokens]"
           "::text[])))")
    N21 = N24.replace("24 tokens", "21 tokens")

    def test_equal_defs_pass(self):
        self.assertEqual(parity_verdict(self.N24, self.N24)[0], "pass")

    def test_diverged_defs_fail_with_diff(self):
        verdict, msg = parity_verdict(self.N24, self.N21)
        self.assertEqual(verdict, "fail")
        self.assertIn("DIVERGENCE", msg)
        self.assertIn("nebula:", msg)
        self.assertIn("scratch:", msg)

    def test_missing_scratch_never_skips(self):
        verdict, msg = parity_verdict(self.N24, "")
        self.assertEqual(verdict, "missing")
        self.assertIn("asymmetry", msg)

    def test_missing_nebula_fails_closed(self):
        self.assertEqual(parity_verdict("", self.N24)[0], "fail")


class LiveParityTest(unittest.TestCase):
    """R5 against a real stack. Skips EXPLICITLY (with reason) when no
    postgres is reachable or only the nebula schema exists (fresh-deploy
    shape); the scratch-absent-while-nebula-checked case FAILS instead."""

    skip_reason = "not probed yet"

    @classmethod
    def setUpClass(cls):
        state = probe_constraint_state()
        if state is None:
            cls.skip_reason = (
                "explicit skip: no reachable postgres with the seeded nexus "
                "DB (hermetic context) — R5 applies to seeded/operator "
                "stacks; run it against the throwaway stack or titanium")
            cls.state = None
            return
        cls.state = state
        if state["scratch_rel"] == "":
            cls.skip_reason = (
                "explicit skip: only the nebula schema exists on this stack "
                "(fresh-deploy shape; ci-bootstrap creates no scratch "
                "schema) — Decision 5 item 4: skip explicitly, not silently")
        else:
            cls.skip_reason = None  # both present: tests actually run

    def _guard(self):
        if self.skip_reason:
            self.skipTest(self.skip_reason)
        if self.state is None:
            self.skipTest("probe state unavailable")

    def test_nebula_constraint_present(self):
        self._guard()
        self.assertNotEqual(
            self.state["nebula_def"], "",
            "nebula.agent_records_role_check missing — source of truth gone")

    def test_scratch_constraint_present_on_mirror_stacks(self):
        self._guard()
        self.assertNotEqual(
            self.state["scratch_rel"], "",
            "scratch schema reachable at class-probe time but now absent")
        self.assertNotEqual(self.state["scratch_def"], "")

    def test_role_check_defs_equal(self):
        self._guard()
        verdict, msg = parity_verdict(
            self.state["nebula_def"], self.state["scratch_def"])
        self.assertEqual(verdict, "pass", msg)


if __name__ == "__main__":
    unittest.main()
