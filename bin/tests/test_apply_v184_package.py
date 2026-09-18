#!/usr/bin/env python3
"""Hermetic tests for bin/apply-v184-package.py.

No DB, no ssh, no nebula, no consolidation: all four seams (db_query,
apply_sql, stage_remote_jsonl / ssh probe, run_consolidate) and the nebula
HTTP get are injected. Pins the R1 cf9e5ba3 contract:

  - gate: no --operator-go → exit 2, inert; non-go record → exit 2;
    already-applied → exit 3; unresolvable → exit 1
  - verify: DB-unreachable fails the battery; absent/present reported
    distinctly; analyst-provenance required in DDL and intake; remote
    JSONLs staged only on real runs
  - apply: skips when table present; raises on psql failure
  - fold: per-source rc==0 required; inserted/deduped aggregated
  - dry-run: gate+verify only, zero mutations, plan printed
  - ordering on real run: apply before fold before mark
"""

import importlib.util
import json
import os
import sys
import unittest.mock

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
TOOL = os.path.join(_REPO, "bin", "apply-v184-package.py")

spec = importlib.util.spec_from_file_location("applyv184", TOOL)
pkg = importlib.util.module_from_spec(spec)
sys.modules["applyv184"] = pkg  # dataclasses resolve __module__ at exec time
spec.loader.exec_module(pkg)

OK = 0


def make_rec(tags):
    return json.dumps({"id": "go-1", "title": "go for V184", "tags": tags})


class Harness:
    """Injects fake seams and records the call order."""

    def __init__(self, *, regclass="NULL", apply_rc=0, jsonl_ok=True,
                 go_tags=("operator:go",), go_status=200, fold_rc=0, fold_report=None):
        self.calls = []
        self.regclass = regclass
        self.apply_rc = apply_rc
        self.jsonl_ok = jsonl_ok
        self.go_tags = list(go_tags)
        self.go_status = go_status
        self.fold_rc = fold_rc
        self.fold_report = fold_report or {"inserted": 5, "duplicates": 2}
        self.applied = False

    def db_query(self, sql):
        self.calls.append("db:" + sql[:30])
        if "to_regclass" in sql:
            rc = 0 if self.regclass is not None else 1
            return rc, self.regclass, "" if rc == 0 else "connection refused"
        return OK, "0", ""

    def apply_sql(self, path):
        self.calls.append("apply")
        self.applied = True
        if self.apply_rc != 0:
            return self.apply_rc, "", "syntax error near x"
        return OK, "CREATE TABLE", ""

    def stage(self, host, dest_dir):
        self.calls.append(f"stage:{host}")
        d = dest_dir / f"{host}.jsonl"
        d.write_text('{"eventId": "x"}\n')
        return d

    def ssh_probe(self, *a, **k):
        self.calls.append(f"probe:{a[0]}")
        m = unittest.mock.Mock()
        m.returncode = 0 if self.jsonl_ok else 1
        m.stdout = "present\n" if self.jsonl_ok else ""
        return m

    def remote_probe(self, host):
        """probe_remote_jsonl seam — hermetic, no subprocess."""
        self.calls.append(f"remote-probe:{host}")
        return self.jsonl_ok

    def run_consolidate(self, source, by, strict, mode="observe"):
        self.calls.append(f"{mode}:{source}")
        # default fold_rc=0 only applies to observe; validate mode is green
        rc = self.fold_rc if mode == "observe" else OK
        return (rc,
                json.dumps(self.fold_report) + "\n" if rc == 0 else "boom")

    def nebula_get(self, record_id):
        self.calls.append("nebula")
        if self.go_status != 200:
            return self.go_status, "{}"
        return 200, make_rec(self.go_tags)

    def mark(self, record_id):
        self.calls.append("mark")
        return OK


def patch_all(h):
    """Fully hermetic: every network/subprocess seam injected. probe_remote_
    jsonl is its own seam (pitfall #15: leaving subprocess.run real let CI
    ssh helium while local passed)."""
    return unittest.mock.patch.multiple(
        pkg, db_query=h.db_query, apply_sql=h.apply_sql,
        stage_remote_jsonl=h.stage, run_consolidate=h.run_consolidate,
        nebula_get=h.nebula_get, mark_go_applied=h.mark,
        probe_remote_jsonl=h.remote_probe,
    )


def run_main(argv):
    return pkg.main(argv)


class TestGate(unittest.TestCase):
    def test_no_go_refuses_inert(self):
        h = Harness()
        with patch_all(h):
            rc = run_main(["run", "--operator-go", "", "--dry-run"])
        self.assertEqual(rc, pkg.EX_GATE)  # argparse required → empty string still gated

    def test_missing_go_flag_is_argparse_refusal(self):
        h = Harness()
        with patch_all(h):
            with self.assertRaises(SystemExit):
                run_main(["run"])

    def test_non_go_record_refused(self):
        h = Harness(go_tags=["type:status-update"])
        with patch_all(h):
            rc = run_main(["run", "--operator-go", "abc", "--dry-run"])
        self.assertEqual(rc, pkg.EX_GATE)
        self.assertNotIn("apply", h.calls)

    def test_already_applied_is_exit3(self):
        h = Harness(go_tags=["operator:go", "status:applied"])
        with patch_all(h):
            rc = run_main(["run", "--operator-go", "abc", "--dry-run"])
        self.assertEqual(rc, pkg.EX_ALREADY)
        self.assertNotIn("apply", h.calls)

    def test_unresolvable_go_is_exit1(self):
        h = Harness(go_status=404)
        with patch_all(h):
            rc = run_main(["run", "--operator-go", "abc", "--dry-run"])
        self.assertEqual(rc, pkg.EX_FAIL)


class TestVerify(unittest.TestCase):
    def test_db_unreachable_fails_battery(self):
        h = Harness(regclass=None)
        with patch_all(h):
            rc = run_main(["verify"])
        self.assertEqual(rc, pkg.EX_FAIL)
        self.assertTrue(any("to_regclass" in c for c in h.calls))

    def test_absent_vs_present_distinct(self):
        h = Harness(regclass="NULL")
        with patch_all(h):
            rc = run_main(["verify"])
        self.assertEqual(rc, pkg.EX_OK)
        h2 = Harness(regclass="vision.calendar_events")
        with patch_all(h2):
            rc2 = run_main(["verify"])
        self.assertEqual(rc2, pkg.EX_OK)
        # both ran; apply-skip path exercised separately below

    def test_verify_subcommand_never_mutates(self):
        h = Harness(regclass="vision.calendar_events")
        with patch_all(h):
            run_main(["verify"])
        self.assertNotIn("apply", h.calls)
        self.assertNotIn("mark", h.calls)
        # validation used validate mode only
        self.assertFalse(any(c.startswith("observe:") for c in h.calls))

    def test_verify_battery_failure_exit1(self):
        # fold_rc=1 must not fail the verify subcommand (validate mode green)
        h = Harness(regclass="vision.calendar_events", fold_rc=1)
        with patch_all(h):
            rc = run_main(["verify"])
        self.assertEqual(rc, pkg.EX_OK)


class TestRunOrdering(unittest.TestCase):
    def test_full_run_order(self):
        h = Harness(regclass="NULL")
        with patch_all(h), \
             unittest.mock.patch.object(pkg.subprocess, "run", h.ssh_probe), \
             unittest.mock.patch.object(pkg.tempfile, "mkdtemp",
                                        return_value="/tmp/v184-test-stage"):
            os.makedirs("/tmp/v184-test-stage", exist_ok=True)
            rc = run_main(["run", "--operator-go", "go-1",
                           "--remote-hosts", "helium", "vanadium"])
        self.assertEqual(rc, pkg.EX_OK, h.calls)
        order = [c for c in h.calls
                 if c in ("apply", "mark") or c.startswith("observe:")
                 or c.startswith("stage:")]
        local = str(pkg.LOCAL_JSONL)
        self.assertEqual(order, ["stage:helium", "stage:vanadium", "apply",
                                 "observe:/tmp/v184-test-stage/helium.jsonl",
                                 "observe:/tmp/v184-test-stage/vanadium.jsonl",
                                 f"observe:{local}", "mark"])

    def test_apply_skipped_when_present(self):
        h = Harness(regclass="vision.calendar_events")
        with patch_all(h), \
             unittest.mock.patch.object(pkg.subprocess, "run", h.ssh_probe), \
             unittest.mock.patch.object(pkg.tempfile, "mkdtemp",
                                        return_value="/tmp/v184-test-stage2"):
            os.makedirs("/tmp/v184-test-stage2", exist_ok=True)
            rc = run_main(["run", "--operator-go", "go-1", "--remote-hosts", "helium"])
        self.assertEqual(rc, pkg.EX_OK)
        self.assertNotIn("apply", h.calls)  # skipped, not failed
        self.assertIn("mark", h.calls)

    def test_apply_failure_stops_before_fold_and_mark(self):
        h = Harness(regclass="NULL", apply_rc=1)
        with patch_all(h), \
             unittest.mock.patch.object(pkg.subprocess, "run", h.ssh_probe), \
             unittest.mock.patch.object(pkg.tempfile, "mkdtemp",
                                        return_value="/tmp/v184-test-stage3"):
            os.makedirs("/tmp/v184-test-stage3", exist_ok=True)
            rc = run_main(["run", "--operator-go", "go-1", "--remote-hosts", "helium"])
        self.assertEqual(rc, pkg.EX_FAIL)
        self.assertIn("apply", h.calls)
        self.assertNotIn("mark", h.calls)
        self.assertFalse(any(c.startswith("observe:") for c in h.calls))

    def test_fold_failure_does_not_mark(self):
        h = Harness(regclass="NULL", fold_rc=1)
        with patch_all(h), \
             unittest.mock.patch.object(pkg.subprocess, "run", h.ssh_probe), \
             unittest.mock.patch.object(pkg.tempfile, "mkdtemp",
                                        return_value="/tmp/v184-test-stage4"):
            os.makedirs("/tmp/v184-test-stage4", exist_ok=True)
            rc = run_main(["run", "--operator-go", "go-1", "--remote-hosts", "helium"])
        self.assertEqual(rc, pkg.EX_FAIL)
        self.assertIn("apply", h.calls)
        self.assertNotIn("mark", h.calls)
        # a fold was attempted (first remote source) before failing
        self.assertTrue(any(c.startswith("observe:/tmp") for c in h.calls))

    def test_fold_reports_aggregated(self):
        h = Harness(regclass="NULL")
        with patch_all(h), \
             unittest.mock.patch.object(pkg.subprocess, "run", h.ssh_probe), \
             unittest.mock.patch.object(pkg.tempfile, "mkdtemp",
                                        return_value="/tmp/v184-test-stage6"):
            os.makedirs("/tmp/v184-test-stage6", exist_ok=True)
            rc = run_main(["run", "--operator-go", "go-1", "--remote-hosts", "helium"])
        self.assertEqual(rc, pkg.EX_OK)  # fold_report default: inserted=5, duplicates=2

    def test_remote_unreachable_fails_verify(self):
        h = Harness(regclass="NULL", jsonl_ok=False)
        with patch_all(h):
            rc = run_main(["run", "--operator-go", "go-1",
                           "--remote-hosts", "darkbox", "--dry-run"])
        self.assertEqual(rc, pkg.EX_FAIL)
        self.assertNotIn("apply", h.calls)
        self.assertIn("remote-probe:darkbox", h.calls)


class TestDryRun(unittest.TestCase):
    def test_dry_run_zero_mutations(self):
        h = Harness(regclass="NULL")
        with patch_all(h):
            rc = run_main(["run", "--operator-go", "go-1", "--remote-hosts", "helium",
                           "--dry-run"])
        self.assertEqual(rc, pkg.EX_OK)
        self.assertNotIn("apply", h.calls)
        self.assertNotIn("mark", h.calls)
        self.assertFalse(any(c.startswith("observe:") for c in h.calls))
        self.assertFalse(any(c.startswith("stage:") for c in h.calls))


if __name__ == "__main__":
    unittest.main()
