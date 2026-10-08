#!/usr/bin/env python3
"""Guard tests for bin/forum_hygiene_sweep.py — the weekly forum-hygiene
sweep (report-only staleness audit wired into the boot shim).

Hermetic: no network, no gh, no services. The module is loaded by file
path; fetchers are stubbed. Pins:

  S1  PR extraction: titles yield distinct PR numbers; non-PR '#' refs and
      empty text yield none
  S2  rating partition: open ratings {0,1,2,3,6} can go stale; resolved
      ratings {4,5,7,8} never produce findings
  S3  core: open thread + MERGED PR -> likely-stale finding with evidence
  S4  core: open thread with NO evidence -> no finding (absence is not a
      claim; report-only philosophy)
  S5  core: resolution record by PR-number match -> needs-review finding
  S6  token fallback: shared distinctive tokens (>=2) match; stopwords and
      1-2 char tokens never count
  S7  weekly gate: no state -> due; fresh state -> not due; old -> due
  S8  state I/O round-trip; corrupt file = first run
  S9  CLI --force with stubbed fetchers: exit 0, state written, report
      printed; --check-only writes nothing
  S10 CLI not-due path: exit 3, no sweep executed
  S11 all-dependencies-down: exit 1, report still renders (degraded)
  S12 boot wiring: Boot.forum_hygiene_step exists, runs between forums()
      and procedures(), degrades safely when the tool is absent, skips on
      --dry-run, and --force flag threads through
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "forum_hygiene_sweep", os.path.join(_SELF_DIR, "..", "forum_hygiene_sweep.py"))
fhs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fhs)

_boot_spec = importlib.util.spec_from_file_location(
    "freebuff_boot_hygiene", os.path.join(_SELF_DIR, "..", "freebuff-boot.py"))
freebuff_boot = importlib.util.module_from_spec(_boot_spec)
_boot_spec.loader.exec_module(freebuff_boot)


def _thread(tid, title, rating=0):
    return {"id": tid, "title": title, "statusRating": rating,
            "createdAt": "2026-09-29T10:00:00Z"}


class TestPrExtraction(unittest.TestCase):
    def test_distinct_prs(self):
        self.assertEqual(fhs.extract_pr_numbers("PR #663 and #663 vs #660"),
                         [660, 663])

    def test_short_refs_ignored(self):
        self.assertEqual(fhs.extract_pr_numbers("ref #1 and #2 only"), [])

    def test_empty(self):
        self.assertEqual(fhs.extract_pr_numbers(""), [])
        self.assertEqual(fhs.extract_pr_numbers(None), [])


class TestRatingPartition(unittest.TestCase):
    def test_partitions_disjoint(self):
        self.assertFalse(fhs.OPEN_RATINGS & fhs.RESOLVED_RATINGS)

    def test_resolved_never_flagged(self):
        for r in (4, 5, 7, 8):
            t = _thread("x", "PR #663 fix", rating=r)
            self.assertEqual(
                fhs.stale_findings([t], {663}, set(), []), [])


class TestCore(unittest.TestCase):
    def test_merged_pr_flags_likely_stale(self):
        t = _thread("t1", "PR #663 — nebula-mcp lockfile broken")
        out = fhs.stale_findings([t], {663}, set(), [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["severity"], "likely-stale")
        self.assertIn("#663 MERGED", out[0]["evidence"][0])

    def test_no_evidence_no_finding(self):
        t = _thread("t2", "Mystery incident nobody named a PR in")
        self.assertEqual(fhs.stale_findings([t], set(), set(), []), [])

    def test_record_match_by_pr_number(self):
        t = _thread("t3", "PR #660 — census fix re-attestation")
        rec = {"id": "323977c2-0000", "role": "tester",
               "title": "ATTESTATION PR #660 — census-bounds guard VERIFIED",
               "createdAt": 1790779055000}
        out = fhs.stale_findings([t], set(), set(), [rec])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["severity"], "needs-review")
        self.assertIn("323977c2", out[0]["evidence"][0])

    def test_record_token_fallback_three_tokens(self):
        t = _thread("t4", "harvests triggers lost live defect")
        rec = {"id": "17907500", "role": "DBA",
               "title": "CLOSED: harvests triggers restored trio live guard",
               "createdAt": "2026-09-29T23:30:00Z"}
        out = fhs.stale_findings([t], set(), set(), [rec])
        self.assertEqual(len(out), 1)

    def test_token_fallback_needs_three(self):
        t = _thread("t5", "alpha beta omega")
        rec = {"id": "r", "role": "DBA", "title": "alpha beta gamma",
               "createdAt": 1}
        self.assertEqual(fhs.stale_findings([t], set(), set(), [rec]), [])

    def test_token_fallback_needs_evidence_role(self):
        t = _thread("t5b", "alpha beta gamma delta")
        rec = {"id": "r", "role": "critic",
               "title": "alpha beta gamma delta echo", "createdAt": 1}
        self.assertEqual(fhs.stale_findings([t], set(), set(), [rec]), [])

    def test_stopwords_and_short_tokens_ignored(self):
        self.assertEqual(fhs.title_tokens("this PR with it to a of"),
                         set())

    def test_open_ratings_partition(self):
        self.assertEqual(fhs.OPEN_RATINGS, {0, 1, 2, 3, 6})


class TestWeeklyGate(unittest.TestCase):
    def test_first_run_due(self):
        self.assertTrue(fhs.weekly_due({}, time.time()))

    def test_fresh_state_not_due(self):
        self.assertFalse(fhs.weekly_due({"last_run_epoch": time.time()},
                                        time.time()))

    def test_old_state_due(self):
        self.assertTrue(fhs.weekly_due(
            {"last_run_epoch": time.time() - fhs.WEEK_SECONDS - 10},
            time.time()))


class TestStateIO(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "state.json")
            fhs.save_state({"last_run_epoch": 17}, p)
            self.assertEqual(fhs.load_state(p)["last_run_epoch"], 17)

    def test_corrupt_state_is_first_run(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "state.json")
            with open(p, "w") as f:
                f.write("{not json")
            self.assertEqual(fhs.load_state(p), {})


class TestCli(unittest.TestCase):
    def _run(self, argv, fetchers=None):
        buf = io.StringIO()
        kwargs = {"fetchers": fetchers} if fetchers else {}
        with mock.patch.object(fhs, "run_sweep", **kwargs):
            with contextlib.redirect_stdout(buf):
                code = fhs.main(argv)
        return code, buf.getvalue()

    def test_force_runs_and_writes_state(self):
        with tempfile.TemporaryDirectory() as td:
            sf = os.path.join(td, "state.json")
            with mock.patch.object(
                    fhs, "run_sweep",
                    return_value={"generated_at": "T", "forums": [],
                                  "threads_scanned": 5, "open_threads": 5,
                                  "stale_found": 1, "findings": [],
                                  "degraded": []}):
                with mock.patch("sys.argv", ["x"]):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        code = fhs.main(["--force", "--state-file", sf])
            self.assertEqual(code, 0)
            self.assertEqual(fhs.load_state(sf)["last_findings"], 1)
            self.assertIn("1 stale", buf.getvalue())

    def test_not_due_exit_3(self):
        with tempfile.TemporaryDirectory() as td:
            sf = os.path.join(td, "state.json")
            fhs.save_state({"last_run_epoch": time.time()}, sf)
            code, out = self._run(["--state-file", sf])
            self.assertEqual(code, 3)
            self.assertIn("not due", out)

    def test_all_deps_down_exit_1(self):
        # Exit mapping: report with every dependency degraded AND zero
        # threads seen -> exit 1 (nothing was reachable).
        report = {"generated_at": "T", "forums": [], "threads_scanned": 0,
                  "open_threads": 0, "stale_found": 0, "findings": [],
                  "degraded": ["issues-and-open-questions: OSError: down",
                               "pr-evidence: OSError", "records: OSError"]}
        with mock.patch.object(fhs, "run_sweep", return_value=report):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = fhs.main(["--force", "--check-only"])
        self.assertEqual(code, 1)
        self.assertIn("degraded", buf.getvalue())

    def test_run_sweep_degrades_on_fetcher_errors(self):
        def boom(*a, **k):
            raise OSError("down")
        report = fhs.run_sweep(threads_fetcher=boom, pr_fetcher=boom,
                               records_fetcher=boom)
        self.assertEqual(report["threads_scanned"], 0)
        self.assertEqual(report["stale_found"], 0)
        self.assertGreaterEqual(len(report["degraded"]), 3)

    def test_check_only_never_writes_state(self):
        with tempfile.TemporaryDirectory() as td:
            sf = os.path.join(td, "state.json")
            with mock.patch.object(
                    fhs, "run_sweep",
                    return_value={"generated_at": "T", "forums": [],
                                  "threads_scanned": 1, "open_threads": 0,
                                  "stale_found": 0, "findings": [],
                                  "degraded": []}):
                code = fhs.main(["--force", "--check-only",
                                 "--state-file", sf])
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(sf))


class TestBootWiring(unittest.TestCase):
    def _boot(self, **kw):
        dry = kw.pop("dry_run", False)
        return freebuff_boot.Boot(role="engineer", model="m", channel="test",
                                  ttl=60, budget=5, lease_policy="skip",
                                  update_pointer=False, limit=10,
                                  dry_run=dry, strict=False, **kw)

    def test_step_exists_and_default_on(self):
        b = self._boot()
        self.assertTrue(b.want_forum_hygiene)

    def test_run_order_between_forums_and_procedures(self):
        b = self._boot()
        import inspect
        src = inspect.getsource(type(b).run)
        self.assertLess(src.index("self.forums()"),
                        src.index("self.forum_hygiene_step()"))
        self.assertLess(src.index("self.forum_hygiene_step()"),
                        src.index("self.procedures()"))

    def test_dry_run_skips(self):
        b = self._boot(dry_run=True)
        b.forum_hygiene_step()
        entry = [s for s in b.steps if s["step"] == "forum-hygiene"][-1]
        self.assertEqual(entry["status"], "skipped")
        self.assertIn("dry-run", entry["detail"])

    def test_opted_out_step_absent(self):
        b = self._boot(want_forum_hygiene=False)
        b.forum_hygiene_step()
        self.assertFalse([s for s in b.steps
                          if s["step"] == "forum-hygiene"])

    def test_tool_absent_degrades(self):
        b = self._boot()
        with mock.patch("os.path.exists", return_value=False):
            b.forum_hygiene_step()
        entry = [s for s in b.steps if s["step"] == "forum-hygiene"][-1]
        self.assertEqual(entry["status"], "degraded")

    def test_wires_force_and_state_dir(self):
        b = self._boot(hygiene_force=True, hygiene_state_dir="/tmp/x")
        self.assertTrue(b.hygiene_force)
        self.assertEqual(b.hygiene_state_dir, "/tmp/x")


if __name__ == "__main__":
    unittest.main()
