#!/usr/bin/env python3
"""Hermetic tests for bin/adapter-health-probe.py (evidence-refresh slice).

No database, no network: the transition law and evidence primitives are pure
functions; DB interactions are driven through fake cursors. Pins the design
contract (R1 d849ff31; drill findings b771c3ec):

- Transition law truth table: active+FAIL -> degraded; degraded+PASS -> active;
  declared promotes ONLY on ADAPTER_PROBE_PROMOTE_DEPTH consecutive PASS;
  declared never degrades; retired is frozen against health; PASS on active
  and FAIL on degraded are no-ops
- Consecutive-pass counting ignores non-PASS tails (SKIP breaks a streak)
- Evidence array: append newest-last, truncate to ADAPTER_PROBE_HISTORY
- Provider dispatch: unknown provider -> SKIP (never a unit failure);
  postgresql check fails safe (FAIL, not raise) on exceptions
- transition_adapter: evidence appended + last_checked set on no-transition;
  CAS rowcount 0 -> cas-conflict stand-down message (operator intent wins);
  transitioned messages carry the promote streak
- --submit-observation: invalid JSON / bad outcome -> exit 1; unknown
  capability/provider -> exit 1; PASS through the seam obeys the same law
- Run-level resilience: a broken run (bad DSN) yields an error DATA row, exit 0

Run:
  python3 -m pytest bin/tests/test_adapter_health_probe.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from unittest import mock

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PROBE = os.path.join(_REPO, "bin", "adapter-health-probe.py")

import pytest

_spec = importlib.util.spec_from_file_location("adapter_health_probe", _PROBE)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

# DB-dependent test classes are skipped where psycopg2 is absent (mesh CI
# matrix without psycopg2); pure-function classes run everywhere.
psycopg2_missing = not probe.PSYCOPG2_AVAILABLE
_db_skip = pytest.mark.skipif(
    psycopg2_missing, reason="psycopg2 not installed — DB-bound surfaces")


def _ev(*results):
    """Observation history from a compact result list."""
    return [{"result": r, "observed_at": f"2026-09-16T00:00:0{i}Z",
             "kind": "health-probe", "observer": "t", "synthetic": False}
            for i, r in enumerate(results)]


class TransitionLawTests(unittest.TestCase):
    """The truth table IS the design — every cell pinned."""

    def test_active_fail_degrades(self):
        self.assertEqual(
            probe.apply_transition_law("active", "FAIL", _ev("PASS", "FAIL")),
            "degraded")

    def test_active_pass_holds(self):
        self.assertIsNone(
            probe.apply_transition_law("active", "PASS", _ev("PASS")))

    def test_degraded_pass_recovers(self):
        self.assertEqual(
            probe.apply_transition_law("degraded", "PASS", _ev("FAIL", "PASS")),
            "active")

    def test_degraded_fail_holds(self):
        self.assertIsNone(
            probe.apply_transition_law("degraded", "FAIL", _ev("FAIL")))

    def test_declared_never_degrades(self):
        self.assertIsNone(
            probe.apply_transition_law("declared", "FAIL", _ev("FAIL")))

    def test_declared_skip_never_promotes(self):
        self.assertIsNone(
            probe.apply_transition_law("declared", "SKIP", _ev("PASS", "SKIP")))

    def test_declared_single_pass_insufficient(self):
        # promote depth default 2 — one pass is optimism, not evidence
        self.assertIsNone(
            probe.apply_transition_law("declared", "PASS", _ev("PASS")))

    def test_declared_promotes_on_depth(self):
        self.assertEqual(
            probe.apply_transition_law("declared", "PASS", _ev("PASS", "PASS")),
            "active")

    def test_declared_promotion_counts_after_prior_passes(self):
        # the freshly appended PASS is already in history when the law runs
        self.assertEqual(
            probe.apply_transition_law("declared", "PASS",
                                       _ev("PASS", "PASS", "PASS")),
            "active")

    def test_retired_frozen_against_health(self):
        self.assertIsNone(probe.apply_transition_law("retired", "PASS",
                                                     _ev("PASS", "PASS")))
        self.assertIsNone(probe.apply_transition_law("retired", "FAIL",
                                                     _ev("FAIL")))

    def test_unknown_status_noop(self):
        self.assertIsNone(probe.apply_transition_law("weird", "PASS", []))


class ConsecutivePassTests(unittest.TestCase):
    def test_trailing_streak(self):
        self.assertEqual(probe._consecutive_passes(_ev("PASS", "PASS")), 2)
        self.assertEqual(probe._consecutive_passes(_ev("FAIL", "PASS")), 1)

    def test_skip_breaks_streak(self):
        self.assertEqual(probe._consecutive_passes(_ev("PASS", "SKIP", "PASS")), 1)

    def test_empty_and_nonlist(self):
        self.assertEqual(probe._consecutive_passes([]), 0)
        self.assertEqual(probe._consecutive_passes(None), 0)
        self.assertEqual(probe._consecutive_passes({"result": "PASS"}), 0)


class EvidenceArrayTests(unittest.TestCase):
    def test_append_newest_last(self):
        out = probe._append_observation(_ev("PASS"), {"result": "FAIL"})
        self.assertEqual([o["result"] for o in out], ["PASS", "FAIL"])

    def test_truncates_to_limit(self):
        probe.HISTORY_LIMIT = 3
        try:
            out = probe._append_observation(
                _ev(*["PASS"] * 5), {"result": "FAIL"})
            self.assertEqual(len(out), 3)
            self.assertEqual(out[-1]["result"], "FAIL")   # newest kept
            self.assertEqual(out[0]["result"], "PASS")    # oldest survivor
        finally:
            probe.HISTORY_LIMIT = 10

    def test_flat_object_normalized_not_dropped(self):
        # defensive parity with the V173 migration: legacy flat evidence is
        # wrapped to a one-element array, then the new observation appended
        out = probe._append_observation({"kind": "legacy"}, {"result": "PASS"})
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {"kind": "legacy"})
        self.assertEqual(out[-1]["result"], "PASS")

    def test_none_evidence_starts_fresh(self):
        out = probe._append_observation(None, {"result": "PASS"})
        self.assertEqual(out, [{"result": "PASS"}])


class FakeCursor:
    """Minimal cursor double: records executes, returns canned rowcounts."""

    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.executed = []

    def execute(self, stmt, params=None):
        self.executed.append((stmt, params))


def _adapter(status="declared", evidence=None):
    return {"id": "ad-1", "provider": "postgresql",
            "adapter_status": status, "evidence": evidence or [],
            "capability": "has-active-shrapnel-protocol"}


@_db_skip
class _Row(dict):
    """Dict row that also tolerates numeric access, mirroring how RealDictRow
    is NOT a tuple — the regression this pins: _resolve_operator_lease must
    dispatch on row shape, never assume tuple or dict."""
    pass


class ResolveLeaseTests(unittest.TestCase):
    def _cur_with(self, row):
        class Cur:
            def execute(self, stmt, params=None):
                pass
            def fetchone(self):
                return row
        return Cur()

    def test_tuple_row_resolved_by_index(self):
        self.assertEqual(
            probe._resolve_operator_lease(self._cur_with(("uuid-1",))),
            "uuid-1")

    def test_dict_row_resolved_by_key(self):
        self.assertEqual(
            probe._resolve_operator_lease(self._cur_with({"id": "uuid-2"})),
            "uuid-2")

    def test_none_row_is_leaseless(self):
        self.assertIsNone(
            probe._resolve_operator_lease(self._cur_with(None)))


class TransitionAdapterTests(unittest.TestCase):
    def test_no_transition_still_records_observation(self):
        cur = FakeCursor()
        kind, msg = probe.transition_adapter(
            cur, _adapter("active"), "PASS", {"check": "t"}, None,
            "leaseless-fallback")
        self.assertEqual(kind, "observed")
        self.assertIn("no transition", msg)
        stmt, params = cur.executed[0]
        self.assertIn("evidence = %s", stmt)
        history = json.loads(params[0])
        self.assertEqual(history[-1]["result"], "PASS")
        self.assertFalse(history[-1]["synthetic"])

    def test_transition_cas_wins(self):
        cur = FakeCursor(rowcount=1)
        kind, msg = probe.transition_adapter(
            cur, _adapter("active"), "FAIL", {}, None, "leaseless-fallback")
        self.assertEqual(kind, "transitioned")
        self.assertIn("active -> degraded", msg)
        stmt, params = cur.executed[0]
        self.assertIn("AND adapter_status = %s", stmt)
        self.assertEqual(params[3], "active")  # CAS guarded on from-status

    def test_cas_conflict_stands_down(self):
        cur = FakeCursor(rowcount=0)
        kind, msg = probe.transition_adapter(
            cur, _adapter("active"), "FAIL", {}, None, "leaseless-fallback")
        self.assertEqual(kind, "cas-conflict")
        self.assertIn("stands down", msg)

    def test_synthetic_flag_round_trips(self):
        cur = FakeCursor()
        probe.transition_adapter(cur, _adapter("degraded", _ev("FAIL")),
                                 "PASS", {}, None,
                                 "standing-lease", synthetic=True,
                                 source="external:drill")
        _, params = cur.executed[0]
        # transition path params: (to_status, evidence_json, id, from_status)
        obs = json.loads(params[1])[-1]
        self.assertTrue(obs["synthetic"])
        self.assertEqual(obs["observer"], "external:drill")

    def test_promote_message_carries_streak(self):
        cur = FakeCursor(rowcount=1)
        kind, msg = probe.transition_adapter(
            # seed history: the fresh PASS appended inside makes the streak 2
            cur, _adapter("declared", _ev("PASS")), "PASS", {}, None,
            "standing-lease")
        self.assertEqual(kind, "transitioned")
        self.assertIn("PASS x2", msg)


class ProviderDispatchTests(unittest.TestCase):
    def test_unknown_provider_skips(self):
        # an unregistered provider (e.g. 'convex') must not be in the table
        self.assertNotIn("convex", probe.PROVIDER_CHECKS)
        self.assertIn("mysql", probe.PROVIDER_CHECKS)
        self.assertIn("postgresql", probe.PROVIDER_CHECKS)

    def test_postgresql_check_fails_safe(self):
        class Boom:
            def cursor(self):
                raise RuntimeError("no connection for you")
        outcome, detail = probe.check_postgresql(Boom(), "cap", {})
        self.assertEqual(outcome, "FAIL")
        self.assertIn("error", detail)

    def test_postgresql_check_pass_counts(self):
        class Cur:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute(self, stmt):
                pass
            def fetchone(self):
                # first call: to_regclass presence; second: count
                self.calls = getattr(self, "calls", 0) + 1
                return (True,) if self.calls == 1 else (4399,)
        class Conn:
            def cursor(self):
                return Cur()
        outcome, detail = probe.check_postgresql(Conn(), "cap", {})
        self.assertEqual(outcome, "PASS")
        self.assertEqual(detail["object_instance_rows"], 4399)


@_db_skip
class SubmitObservationTests(unittest.TestCase):
    def _run(self, stdin_text, fetch=None):
        class Cur:
            rowcount = 1
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute(self, stmt, params=None):
                pass
            def fetchone(self):
                return fetch
        class Conn:
            def cursor(self, *a, **k):
                return Cur()
            def commit(self):
                pass
            def close(self):
                pass
        with mock.patch.object(probe.sys, "stdin",
                               mock.Mock(read=lambda: stdin_text)), \
             mock.patch.object(probe.psycopg2, "connect",
                               return_value=Conn()), \
             mock.patch.object(probe, "_resolve_operator_lease",
                               return_value=None), \
             mock.patch.object(probe.psycopg2.extras,
                               "RealDictCursor", object()):
            return probe.submit_observation_stream()
            return probe.submit_observation_stream()

    def test_invalid_json_exits_1(self):
        self.assertEqual(self._run("not json"), 1)

    def test_bad_outcome_exits_1(self):
        bad = json.dumps({"source": "s", "capability": "c",
                          "provider": "postgresql", "outcome": "MAYBE"})
        self.assertEqual(self._run(bad), 1)

    def test_missing_capability_exits_1(self):
        bad = json.dumps({"provider": "postgresql", "outcome": "PASS"})
        self.assertEqual(self._run(bad), 1)

    def test_unknown_adapter_exits_1(self):
        ok = json.dumps({"source": "profiles", "capability": "cap",
                         "provider": "postgresql", "outcome": "PASS"})
        with mock.patch.object(probe.psycopg2.extras,
                               "RealDictCursor", object()):
            self.assertEqual(self._run(ok, fetch=None), 1)

    def test_valid_observation_transitions(self):
        ok = json.dumps({"source": "profiles", "capability": "cap",
                         "provider": "postgresql", "outcome": "FAIL",
                         "synthetic": False})
        with mock.patch.object(probe.psycopg2.extras,
                               "RealDictCursor", object()):
            self.assertEqual(
                self._run(ok, fetch=_adapter("active")), 0)


@_db_skip
class MysqlCheckTests(unittest.TestCase):
    """Every branch of check_mysql — SKIPs are data, not failures."""

    def _with_env(self, **kv):
        return mock.patch.dict(probe.os.environ, kv, clear=False)

    def test_no_dsn_skips_with_reason(self):
        with self._with_env(ADAPTER_PROBE_MYSQL_DSN=""):
            outcome, detail = probe.check_mysql(None, "cap", {})
        self.assertEqual(outcome, "SKIP")
        self.assertIn("ADAPTER_PROBE_MYSQL_DSN not configured", detail["reason"])

    def test_no_driver_skips_with_reason(self):
        with self._with_env(ADAPTER_PROBE_MYSQL_DSN="mysql://u:p@h:3306/db"), \
             mock.patch.object(probe, "_import_driver", return_value=(None,
                                    ImportError("no driver anywhere"))):
            outcome, detail = probe.check_mysql(None, "cap", {})
        self.assertEqual(outcome, "SKIP")
        self.assertIn("no mysql driver", detail["reason"])

    def test_connect_failure_is_fail(self):
        class CM:
            def __enter__(self):
                raise RuntimeError("can't reach host")
            def __exit__(self, *a):
                return False
        with self._with_env(ADAPTER_PROBE_MYSQL_DSN="mysql://u:p@h:3306/db"), \
             mock.patch.object(probe, "_mysql_connection", return_value=CM()):
            outcome, detail = probe.check_mysql(None, "cap", {})
        self.assertEqual(outcome, "FAIL")
        self.assertIn("can't reach host", detail["error"])

    def test_pass_without_surface(self):
        class Cur:
            connection = mock.Mock()
            def execute(self, stmt, params=None):
                pass
            def fetchone(self):
                return (1,)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        class CM:
            def __enter__(self):
                return Cur()
            def __exit__(self, *a):
                return False
        with self._with_env(ADAPTER_PROBE_MYSQL_DSN="mysql://u:p@h:3306/db"), \
             mock.patch.object(probe, "_mysql_connection", return_value=CM()):
            outcome, detail = probe.check_mysql(None, "cap", {})
        self.assertEqual(outcome, "PASS")
        self.assertEqual(detail["check"], "mysql-connect")

    def test_pass_with_surface_rowcount(self):
        class Cur:
            connection = mock.Mock()
            def __init__(self):
                self.calls = 0
            def execute(self, stmt, params=None):
                self.calls += 1
            def fetchone(self):
                return (1234,)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        class CM:
            def __enter__(self):
                return Cur()
            def __exit__(self, *a):
                return False
        with self._with_env(ADAPTER_PROBE_MYSQL_DSN="mysql://u:p@h:3306/db",
                            ADAPTER_PROBE_MYSQL_SURFACE="shrapnel.object_instance"), \
             mock.patch.object(probe, "_mysql_connection", return_value=CM()):
            outcome, detail = probe.check_mysql(None, "cap", {})
        self.assertEqual(outcome, "PASS")
        self.assertEqual(detail["approx_rows"], 1234)

    def test_absent_surface_is_fail(self):
        class Cur:
            connection = mock.Mock()
            def execute(self, stmt, params=None):
                pass
            def fetchone(self):
                return None
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        class CM:
            def __enter__(self):
                return Cur()
            def __exit__(self, *a):
                return False
        with self._with_env(ADAPTER_PROBE_MYSQL_DSN="mysql://u:p@h:3306/db",
                            ADAPTER_PROBE_MYSQL_SURFACE="shrapnel.object_instance"), \
             mock.patch.object(probe, "_mysql_connection", return_value=CM()):
            outcome, detail = probe.check_mysql(None, "cap", {})
        self.assertEqual(outcome, "FAIL")
        self.assertIn("absent", detail["error"])

    def test_dispatch_table_has_mysql(self):
        self.assertIn("mysql", probe.PROVIDER_CHECKS)
        self.assertIn("postgresql", probe.PROVIDER_CHECKS)


class RunResilienceTests(unittest.TestCase):
    def test_broken_run_yields_error_row_not_raise(self):
        with mock.patch.object(probe.psycopg2, "connect",
                               side_effect=RuntimeError("db down")):
            results = probe.run_probe()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["kind"], "error")
        self.assertIn("db down", results[0]["message"])


if __name__ == "__main__":
    unittest.main()
