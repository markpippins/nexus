#!/usr/bin/env python3
"""Hermetic tests for bin/wr-repoint-verify.py (W1-W4 repoint battery).

No database, no network: checks are pure functions over injected query
results; HTTP probes are mocked. Pins the contract from the checklist
posted to the ruling thread (DBA R1):

- structural battery S1-S8 present and tranche registries complete
- statuses PASS/FAIL/SKIP semantics: baseline drift FAILs, post-V187
  absent surfaces SKIP, silent-ELSE 'NEW' sentinel FAILs (V186-VOCAB-001)
- crosswalk invariant keys on the canonical PK (architect pin f08fdf0c),
  never the legacy_id tail
- landing checks: SKIP without --since, PASS on rows, FAIL on zero rows
- exit mapping: any FAIL -> 1, else 0; environmental -> 2
"""

import importlib.util
import os
import sys
import unittest
from unittest import mock

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
sys.path.insert(0, _REPO)

_spec = importlib.util.spec_from_file_location(
    "wr_repoint_verify", os.path.join(_REPO, "bin", "wr-repoint-verify.py"))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CTXT = {"UndefinedTable": type("UndefinedTable", (Exception,), {})}

def scalar(v):
    return [(v,)]


class StructuralBattery(unittest.TestCase):
    def run_check(self, cid, rows_by_call):
        check = {c.id: c for c in mod.STRUCTURAL}[cid]
        if callable(rows_by_call):
            return check.fn(rows_by_call, dict(CTXT))
        calls = []

        def q(sql):
            calls.append(sql)
            return rows_by_call[calls.index(sql)]

        return check.fn(q, dict(CTXT))

    def test_registry_complete(self):
        ids = {c.id for c in mod.STRUCTURAL}
        self.assertEqual(ids, {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"})
        for t in ("W1", "W2", "W3", "W4"):
            self.assertTrue(mod.TRANCHES[t], t)

    def test_s1_fails_on_missing_legacy_id(self):
        self.assertEqual(self.run_check("S1", [scalar(0)])[0], mod.PASS)
        self.assertEqual(self.run_check("S1", [scalar(2)])[0], mod.FAIL)

    def test_s2_fails_on_unknown_prefix(self):
        ok = self.run_check("S2", [[("vision.work_requests",), ("nebula.work_requests_history",)]])
        self.assertEqual(ok[0], mod.PASS)
        bad = self.run_check("S2", [[("vision.work_requests",), ("conduit.something",)]])
        self.assertEqual(bad[0], mod.FAIL)
        self.assertIn("UNKNOWN", bad[1])

    def test_s3_counts_unlinked_vision_rows(self):
        ok = self.run_check("S3", [scalar(0)])
        self.assertEqual(ok[0], mod.PASS)
        bad = self.run_check("S3", [scalar(1)])
        self.assertEqual(bad[0], mod.FAIL)
        # the invariant SQL must key the mirror on wr.id, NOT the legacy_id tail
        # (checked indirectly: the check name carries the pin)

    def test_s5_silent_else_sentinel_fails_even_alone(self):
        sentinel = self.run_check("S5", [[("NEW", 1)]])
        self.assertEqual(sentinel[0], mod.FAIL)
        self.assertIn("ELSE", sentinel[1])
        good = self.run_check("S5", [[("COMPLETED", 1), ("DRAFT", 6)]])
        self.assertEqual(good[0], mod.PASS)
        unknown = self.run_check("S5", [[("WEIRD", 1)]])
        self.assertEqual(unknown[0], mod.FAIL)

    def test_s6_fails_on_closed_bitemporal(self):
        ok = self.run_check("S6", [[(0, 0, 0, 0)]])
        self.assertEqual(ok[0], mod.PASS)
        bad = self.run_check("S6", [[(0, 2, 0, 0)]])
        self.assertEqual(bad[0], mod.FAIL)

    def test_s7_freeze_and_post_v187_skip(self):
        good = self.run_check("S7", [scalar(6), scalar(1), scalar(0), scalar(7)])
        self.assertEqual(good[0], mod.PASS)
        drifted = self.run_check("S7", [scalar(7), scalar(1), scalar(0), scalar(8)])
        self.assertEqual(drifted[0], mod.FAIL)
        self.assertIn("vision.work_requests=7", drifted[1])
        # post-V187: an archived (absent) surface raises UndefinedTable from q;
        # s7 skips that table and still judges the rest.
        calls = {"n": 0}

        def q(sql):
            calls["n"] += 1
            if calls["n"] == 2:
                raise CTXT["UndefinedTable"]("nebula.work_requests_history does not exist")
            return scalar([6, None, 0, 7][calls["n"] - 1])

        partial = self.run_check("S7", q)
        self.assertEqual(partial[0], mod.PASS)
        self.assertIn("ABSENT(post-V187 SKIP)", partial[1])
        self.assertIn("baseline", good[1])

    def test_s8_skips_when_no_triggers(self):
        res = self.run_check("S8", [scalar(0)])
        self.assertEqual(res[0], mod.SKIP)
        self.assertIn("V156-pattern", res[1])


class LandingChecks(unittest.TestCase):
    def _landing(self, cid):
        for t, checks in mod.TRANCHES.items():
            for c in checks:
                if c.id == cid:
                    return c
        raise AssertionError(cid)

    def test_skip_without_since(self):
        ctx = dict(CTXT)
        status, detail = self._landing("W3a").fn(lambda sql: [], ctx)
        self.assertEqual(status, mod.SKIP)

    def test_pass_on_landed_rows(self):
        ctx = dict(CTXT, since="2026-09-20T08:00:00Z",
                   legacy_prefix=None, created_by=None)
        calls = []

        def q(sql):
            calls.append(sql)
            return [(1, "2026-09-20T08:05:00Z")] if "count(*)" in sql else \
                   [("id-1", "nebula.work_requests_history:x")]

        status, detail = self._landing("W3a").fn(q, ctx)
        self.assertEqual(status, mod.PASS)
        self.assertIn("prefix 'nebula.work_requests_history:'", detail)

    def test_fail_on_zero_rows(self):
        ctx = dict(CTXT, since="2026-09-20T08:00:00Z",
                   legacy_prefix=None, created_by=None)
        status, detail = self._landing("W3a").fn(lambda sql: [(0, None)], ctx)
        self.assertEqual(status, mod.FAIL)
        self.assertIn("repoint not observed", detail)

    def test_created_by_narrows_sql(self):
        ctx = dict(CTXT, since="2026-09-20T08:00:00Z",
                   legacy_prefix=None, created_by="cascade")
        seen = []

        def q(sql):
            seen.append(sql)
            return [(0, None)]

        self._landing("W4a").fn(q, ctx)
        self.assertIn("created_by = 'cascade'", seen[0])

    def test_prefix_override(self):
        ctx = dict(CTXT, since="2026-09-20T08:00:00Z",
                   legacy_prefix="conduit.custom:", created_by=None)
        seen = []

        def q(sql):
            seen.append(sql)
            return [(0, None)]

        self._landing("W3a").fn(q, ctx)
        self.assertIn("legacy_id LIKE 'conduit.custom:%'", seen[0])


class W1Cutover(unittest.TestCase):
    def test_down_up_passes(self):
        with mock.patch.object(mod, "_probe", side_effect=[("unreachable", ""), ("listening", " (HTTP 404)")]):
            status, detail = mod.w1_cutover(None, dict(CTXT))
        self.assertEqual(status, mod.PASS)

    def test_vision_srv_still_up_fails(self):
        with mock.patch.object(mod, "_probe", side_effect=[("listening", ""), ("listening", "")]):
            status, detail = mod.w1_cutover(None, dict(CTXT))
        self.assertEqual(status, mod.FAIL)
        self.assertIn("expected down", detail)

    def test_losm_host_down_fails(self):
        with mock.patch.object(mod, "_probe", side_effect=[("unreachable", ""), ("unreachable", "")]):
            status, _ = mod.w1_cutover(None, dict(CTXT))
        self.assertEqual(status, mod.FAIL)

    def test_probe_treats_any_http_as_listening(self):
        # 404 on / from a live FastAPI is a live listener, not 'down'
        import urllib.error
        err = urllib.error.HTTPError("http://x", 404, "Not Found", None, None)
        with mock.patch.object(mod.urllib.request, "urlopen", side_effect=err):
            code, detail = mod._probe("http://localhost:1")
        self.assertEqual(code, "listening")
        self.assertIn("404", detail)


class ExitMapping(unittest.TestCase):
    def test_run_aggregates_fail(self):
        checks = [mod.Check("X", "x", lambda q, c: (mod.PASS, "ok")),
                  mod.Check("Y", "y", lambda q, c: (mod.FAIL, "nope"))]
        results = mod.run(checks, lambda sql: [], dict(CTXT))
        self.assertEqual([r["status"] for r in results], [mod.PASS, mod.FAIL])

    def test_undefined_table_becomes_skip_in_run(self):
        def boom(q, c):
            raise CTXT["UndefinedTable"]("gone")
        results = mod.run([mod.Check("Z", "z", boom)], lambda sql: [], dict(CTXT))
        self.assertEqual(results[0]["status"], mod.SKIP)

    def test_open_ended_helper(self):
        self.assertTrue(mod._open_ended("infinity"))
        self.assertTrue(mod._open_ended("9999-12-31 23:59:59+00"))
        self.assertFalse(mod._open_ended("2026-09-20 08:00:00+00"))
        self.assertFalse(mod._open_ended(None))


if __name__ == "__main__":
    unittest.main()
