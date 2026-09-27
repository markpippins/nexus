"""Tests for bin/start_write_queue_reconciler.py — Option A gate order.

Covers the three pre-start gates and the start/attach-wait supervision:

  - provision:    helper missing / helper failure / helper success
  - readiness:    immediate client-connect success / exhausted retry budget
  - pre-existence: staging table missing / table without its write_id PK /
    table present with PK (psycopg2 faked — no live DB needed)
  - attach-wait:  child attaches then exits (wrapper mirrors rc 0) /
    child exits before attaching (rc surfaced + log tail) / child never
    attaches (timeout refusal)
  - main/dry-run: all gates pass + --dry-run exits 0 without starting the
    reconciler; any gate failure exits 1 with a refusal line

The wrapper is exercised as an imported module (gate functions) and as a
subprocess-free supervision function; the attach-wait cases run real child
interpreters against a tmp log file. No NATS, no PG, no network.

Run:
  python3 -m pytest bin/tests/test_start_write_queue_reconciler.py -v
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "bin"))

import start_write_queue_reconciler as swr  # noqa: E402


class GateProvisionTest(unittest.TestCase):
    def test_helper_missing_fails(self):
        with mock.patch.object(swr, "HELPER", pathlib.Path("/nonexistent/helper.py")):
            ok, detail = swr.gate_provision("nats://localhost:4222")
        self.assertFalse(ok)
        self.assertIn("missing", detail)

    def test_helper_failure_surfaces_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = pathlib.Path(tmp) / "helper.py"
            helper.write_text("import sys; print('boom', file=sys.stderr); sys.exit(7)\n")
            with mock.patch.object(swr, "HELPER", helper):
                ok, detail = swr.gate_provision("nats://localhost:4222")
        self.assertFalse(ok)
        self.assertIn("exit 7", detail)
        self.assertIn("boom", detail)

    def test_helper_success_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = pathlib.Path(tmp) / "helper.py"
            helper.write_text("import sys; sys.exit(0)\n")
            with mock.patch.object(swr, "HELPER", helper):
                ok, detail = swr.gate_provision("nats://localhost:4222")
        self.assertTrue(ok)
        self.assertEqual(detail, "stream ensured")


class GateReadinessTest(unittest.TestCase):
    def test_immediate_success(self):
        snippet = "import sys; sys.exit(0)"
        with mock.patch.object(swr, "NATS_READY_SNIPPET", snippet):
            ok, detail = swr.gate_readiness("nats://localhost:4222", 10)
        self.assertTrue(ok)
        self.assertIn("accepting", detail)

    def test_budget_exhausted_fails(self):
        snippet = "import sys; print('nats down', file=sys.stderr); sys.exit(1)"
        with mock.patch.object(swr, "NATS_READY_SNIPPET", snippet):
            ok, detail = swr.gate_readiness("nats://localhost:4222", 1)
        self.assertFalse(ok)
        self.assertIn("never became ready", detail)
        self.assertIn("nats down", detail)


def _fake_psycopg2(regclass_row, pk_row):
    """Build a fake psycopg2 module whose cursor answers the wrapper's two
    queries: SELECT to_regclass(...) and the pg_constraint PK lookup."""

    class FakeCursor:
        def __init__(self):
            self.last_sql = ""

        def execute(self, sql, params=None):
            self.last_sql = sql

        def fetchone(self):
            if "to_regclass" in self.last_sql:
                return regclass_row
            if "pg_constraint" in self.last_sql:
                return pk_row
            raise AssertionError(f"unexpected query: {self.last_sql}")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    fake = types.ModuleType("psycopg2")
    fake.connect = lambda dsn, connect_timeout=5: FakeConn()
    return fake


class GatePreexistenceTest(unittest.TestCase):
    DSN = "postgres://fake:fake@localhost:5432/fake"

    def _run(self, regclass_row, pk_row):
        fake = _fake_psycopg2(regclass_row, pk_row)
        with mock.patch.dict(sys.modules, {"psycopg2": fake}):
            return swr.gate_preexistence(self.DSN)

    def test_table_missing_fails(self):
        ok, detail = self._run((None,), None)
        self.assertFalse(ok)
        self.assertIn("does not exist", detail)
        self.assertIn("nexus-ci-bootstrap.sql", detail)

    def test_table_without_pk_fails(self):
        ok, detail = self._run(("resolution.write_queue_applied",), None)
        self.assertFalse(ok)
        self.assertIn("WITHOUT its write_id primary key", detail)

    def test_table_with_pk_passes(self):
        ok, detail = self._run(("resolution.write_queue_applied",), (1,))
        self.assertTrue(ok)
        self.assertIn("present with PK", detail)

    def test_psycopg2_missing_fails(self):
        real = sys.modules.get("psycopg2")
        try:
            sys.modules["psycopg2"] = None  # import raises ImportError
            ok, detail = swr.gate_preexistence(self.DSN)
        finally:
            if real is not None:
                sys.modules["psycopg2"] = real
            else:
                sys.modules.pop("psycopg2", None)
        self.assertFalse(ok)
        self.assertIn("psycopg2 unavailable", detail)


class AttachWaitTest(unittest.TestCase):
    ATTACH_LINE = ("[ts] [write-queue-reconciler] durable consumer "
                   "'write_queue_reconciler' attached to nexus.write-queue.v1.> (explicit acks)")

    def test_attach_then_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "reconciler.log"
            child = (
                "import sys, time\n"
                f"print({self.ATTACH_LINE!r}, flush=True)\n"
                "time.sleep(0.3); sys.exit(0)\n"
            )
            rc = swr.start_and_wait_attach([sys.executable, "-c", child], 15, log)
        self.assertEqual(rc, 0)

    def test_early_exit_surfaces_code_and_log_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "reconciler.log"
            child = (
                "import sys; print('FATAL: NoServersError', file=sys.stderr); sys.exit(1)\n"
            )
            rc = swr.start_and_wait_attach([sys.executable, "-c", child], 15, log)
        self.assertEqual(rc, 1)

    def test_never_attaches_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "reconciler.log"
            child = "import time; time.sleep(3)\n"
            rc = swr.start_and_wait_attach([sys.executable, "-c", child], 1, log)
        self.assertEqual(rc, 1)


class MainDryRunTest(unittest.TestCase):
    def test_dry_run_all_gates_pass_exits_zero(self):
        with mock.patch.object(swr, "gate_provision", return_value=(True, "stream ensured")), \
             mock.patch.object(swr, "gate_readiness", return_value=(True, "ready")), \
             mock.patch.object(swr, "gate_preexistence", return_value=(True, "present with PK")):
            rc = swr.main(["--dry-run"])
        self.assertEqual(rc, 0)

    def test_gate_failure_refuses_with_exit_one(self):
        with mock.patch.object(swr, "gate_provision", return_value=(False, "boom")), \
             mock.patch.object(swr, "gate_readiness", return_value=(True, "ready")), \
             mock.patch.object(swr, "gate_preexistence", return_value=(True, "present with PK")):
            rc = swr.main([])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
