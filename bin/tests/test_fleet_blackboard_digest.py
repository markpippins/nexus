#!/usr/bin/env python3
"""Tests for bin/fleet-blackboard-digest.py (daily fleet blackboard → record).

Mirrors test_boot_blackboard.py conventions: the runner is loaded by file
path, a fake continuity.blackboard module is injected into sys.modules, and
every disposition is pinned. The timer must never fail on environmental
conditions — degraded renders are data.

Pins:

  R1  resolve_roles db-union: distinct roles from blackboard view UNION
      agent_records, source=db-union
  R2  resolve_roles DB error -> governed fallback list, source=db-error->fallback
  R3  resolve_roles env override FLEET_BLACKBOARD_FALLBACK_ROLES wins on error
  R4  resolve_roles DB reachable but empty -> fallback, source=db-empty->fallback
  R5  render_fleet calls render_role_digest with advance=False for EVERY role
      (read-only stance: a timer must never advance checkpoints) and tags
      each result with its role
  R6  render_fleet never raises when a role render errors (degraded is data)
  F1  format_fleet renders the counts table (todo:AN / stale / inbox:new /
      seen / ckpt:never) and per-role digest blocks
  F2  format_fleet renders reason for non-ok statuses
  P1  file_record returns the record id on 201
  P2  file_record returns None (never raises) when nebula is unreachable
  M1  main --print renders to stdout and POSTs nothing
  M2  main default path POSTs the record and exits 0
  M3  main exits 0 even when record filing fails (failures are data)
  M4  main --json emits machine-readable summary
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))

_OK = {"status": "ok", "cache": "miss",
       "digest": {"counts": {"todo_action_needed": 2, "todo_stale": 1,
                             "inbox_action_needed": 7, "inbox_seen": 1439,
                             "checkpoints_never_reviewed": 0}},
       "format": "blackboard [X]: todo action-needed=2"}
_DEGRADED = {"status": "degraded", "reason": "pg down"}


def _install_fake_blackboard(render_results=None, render_error=None):
    """Inject a fake continuity.blackboard; returns (captured, cleanup)."""
    captured = {"calls": []}
    results = render_results or {}

    def _render(role, dsn, cache=None, advance=False, model=None, **k):
        captured["calls"].append((role, advance))
        if render_error is not None:
            raise render_error
        return dict(results.get(role, _OK))

    pkg = types.ModuleType("continuity")
    pkg.__path__ = []
    mod = types.ModuleType("continuity.blackboard")
    mod.render_role_digest = _render
    mod.RedisCache = lambda **k: object()
    pkg.blackboard = mod
    sys.modules["continuity"] = pkg
    sys.modules["continuity.blackboard"] = mod

    def cleanup():
        for name in ("continuity", "continuity.blackboard"):
            sys.modules.pop(name, None)

    return captured, cleanup


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "fleet_blackboard_digest",
        os.path.join(_SELF_DIR, "..", "fleet-blackboard-digest.py"))
    mod = module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_fake_psycopg2(rows=None, error=None):
    if error is not None:
        def _connect(*a, **k):
            raise error
    else:
        def _connect(*a, **k):
            return FakeConn(rows)
    mod = types.ModuleType("psycopg2")
    mod.connect = _connect
    sys.modules["psycopg2"] = mod

    def cleanup():
        sys.modules.pop("psycopg2", None)

    return cleanup


class ResolveRolesTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("FLEET_BLACKBOARD_FALLBACK_ROLES", None)
        for name in ("continuity", "continuity.blackboard"):
            sys.modules.pop(name, None)

    def tearDown(self):
        os.environ.pop("FLEET_BLACKBOARD_FALLBACK_ROLES", None)
        for name in ("continuity", "continuity.blackboard"):
            sys.modules.pop(name, None)

    def test_R1_db_union(self):
        mod = _load_script()
        cl = _install_fake_psycopg2(rows=[("engineer",), ("DBA",), (None,),
                                          ("engineer",)])
        try:
            roles, source = mod.resolve_roles("dsn")
        finally:
            cl()
        self.assertEqual(roles, ["DBA", "engineer"])
        self.assertEqual(source, "db-union")

    def test_R2_db_error_falls_back(self):
        mod = _load_script()
        cl = _install_fake_psycopg2(error=RuntimeError("no pg"))
        try:
            roles, source = mod.resolve_roles("dsn")
        finally:
            cl()
        self.assertIn("architect", roles)
        self.assertEqual(source, "db-error->fallback")

    def test_R3_env_override_on_error(self):
        os.environ["FLEET_BLACKBOARD_FALLBACK_ROLES"] = "alpha,beta"
        mod = _load_script()
        cl = _install_fake_psycopg2(error=RuntimeError("no pg"))
        try:
            roles, source = mod.resolve_roles("dsn")
        finally:
            cl()
        self.assertEqual(roles, ["alpha", "beta"])
        self.assertEqual(source, "db-error->fallback")

    def test_R4_db_empty_falls_back(self):
        mod = _load_script()
        cl = _install_fake_psycopg2(rows=[])
        try:
            roles, source = mod.resolve_roles("dsn")
        finally:
            cl()
        self.assertEqual(source, "db-empty->fallback")


class RenderFleetTests(unittest.TestCase):
    def test_R5_read_only_advance_false_every_role(self):
        captured, cleanup = _install_fake_blackboard()
        mod = _load_script()
        try:
            results, notes = mod.render_fleet(["engineer", "DBA"])
        finally:
            cleanup()
        self.assertEqual(captured["calls"],
                         [("engineer", False), ("DBA", False)])
        self.assertEqual([r["role"] for r in results], ["engineer", "DBA"])
        self.assertIn("engineer: ok", notes)

    def test_R6_render_error_is_data_not_raise(self):
        _captured, cleanup = _install_fake_blackboard(render_error=RuntimeError("boom"))
        mod = _load_script()
        try:
            results, _notes = mod.render_fleet(["engineer"])
        finally:
            cleanup()
        self.assertEqual(results[0]["status"], "error")


class FormatFleetTests(unittest.TestCase):
    def test_F1_counts_table(self):
        mod = _load_script()
        out = mod.format_fleet([dict(_OK, role="engineer")])
        self.assertIn("| role | status | todo:AN | todo:stale | inbox:new |", out)
        self.assertIn("| engineer | ok | 2 | 1 | 7 | 1439 | 0 |", out)
        self.assertIn("read-only (no checkpoint advance)", out)

    def test_F2_reason_rendered_for_non_ok(self):
        mod = _load_script()
        out = mod.format_fleet([dict(_DEGRADED, role="planner")])
        self.assertIn("## planner — degraded", out)
        self.assertIn("pg down", out)


class FileRecordTests(unittest.TestCase):
    def _response(self, rid="rec-1"):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps({"id": rid}).encode()
        return resp

    def test_P1_files_record(self):
        mod = _load_script()
        with mock.patch("urllib.request.urlopen",
                        return_value=self._response()):
            rid = mod.file_record("# digest", ["engineer"])
        self.assertEqual(rid, "rec-1")

    def test_P2_nebula_down_returns_none(self):
        mod = _load_script()
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("refused")):
            rid = mod.file_record("# digest", ["engineer"])
        self.assertIsNone(rid)


class MainTests(unittest.TestCase):
    def _run(self, mod, argv):
        captured, cleanup = _install_fake_blackboard()
        try:
            with mock.patch.object(sys, "argv", ["x"] + argv), \
                 contextlib.redirect_stdout(io.StringIO()) as out, \
                 mock.patch("urllib.request.urlopen") as up:
                up.return_value = FileRecordTests._response(self, "rec-9")
                rc = mod.main()
            return rc, out.getvalue(), up
        finally:
            cleanup()

    def test_M1_print_posts_nothing(self):
        mod = _load_script()
        rc, out, up = self._run(mod, ["--print"])
        self.assertEqual(rc, 0)
        self.assertIn("# Fleet blackboard digest", out)
        up.assert_not_called()

    def test_M2_default_files_record_and_exits_zero(self):
        mod = _load_script()
        rc, _out, up = self._run(mod, [])
        self.assertEqual(rc, 0)
        body = json.loads(up.call_args.args[0].data.decode())
        self.assertEqual(body["role"], "DBA")
        self.assertEqual(body["recordType"], "inspection")
        self.assertIn("source:fleet-blackboard", body["tags"])

    def test_M3_record_failure_still_exits_zero(self):
        mod = _load_script()
        captured, cleanup = _install_fake_blackboard()
        try:
            with mock.patch.object(sys, "argv", ["x"]), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 mock.patch("urllib.request.urlopen",
                            side_effect=OSError("refused")):
                rc = mod.main()
            self.assertEqual(rc, 0)
        finally:
            cleanup()

    def test_M4_json_output(self):
        mod = _load_script()
        rc, out, up = self._run(mod, ["--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("roles", payload)
        self.assertIn("results", payload)
        up.assert_not_called()


if __name__ == "__main__":
    unittest.main()
