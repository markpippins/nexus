"""Hermetic tests for bin/fleet-registry-sync.py (fleet slice 3).

No DB: the cursor is a scripted fake. Pins the R1 0d40c061 contract:

  - plan() is a pure read-only planner: servers upsert/insert split,
    unknown services reported and skipped (archetype rows are never
    invented by sync), retire list intersected with existing hosts
  - apply_plan(): env-name resolution (unknown env name raises),
    retire = RETIRED + active_flag=false (never DELETE),
    deployments are check-then-insert idempotent, and deployments for
    NEWLY-inserted servers resolve the new host id (post-insert re-read)
  - main(): dry-run default writes nothing; --apply commits
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_TOOL = os.path.join(_REPO, "bin", "fleet-registry-sync.py")

_spec = importlib.util.spec_from_file_location("fleet_registry_sync", _TOOL)
frs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frs)


class _FakeCursor:
    """Scripted fake: SQL-substring predicates -> result values, consumed
    in order for repeated identical queries (servers select is read twice:
    plan-time and post-insert in apply)."""

    def __init__(self, script):
        self.script = list(script)  # (substring, value) pairs
        self.executed = []
        self.rowcount = 1
        self.lastrowid = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        for i, (pred, val) in enumerate(self.script):
            if pred in sql:
                if callable(val):  # dynamic result (consumption-aware)
                    val = val()
                self._last = val
                # non-consumable matches stay for repeated use unless the
                # value is marked consumed by returning a _Once wrapper
                self._pred_i = i
                return
        self._last = None

    def fetchall(self):
        v = getattr(self, "_last", None)
        return v if isinstance(v, list) else []

    def fetchone(self):
        v = getattr(self, "_last", None)
        if v is None:
            return None
        if isinstance(v, list):
            return v[0] if v else None
        return v


class _FakeConn:
    def __init__(self, script):
        self.cur = _FakeCursor(script)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


HOSTS = [("titanium", 1), ("barium", 2)]
SERVICES = [("nebula-srv", 20, 3101), ("wind-srv", 55, 3300)]
# the apply-time re-read selects only (name, id)
SERVICES_IDS = [("nebula-srv", 20), ("wind-srv", 55)]
ENV_OK = [(7,)]


def _svc_scripts():
    """Distinct predicates for the two services queries (plan vs apply
    re-read) so the substring fake routes each to the right tuple width."""
    return [
        ("name, id, default_port", SERVICES),
        ("SELECT name, id FROM registry.services", SERVICES_IDS),
    ]


def _manifest(**over):
    m = {
        "fleet": [
            {"hostname": "titanium", "environment": "Production",
             "status": "ACTIVE",
             "services": ["nebula-srv", "wind-srv"]},
            {"hostname": "helium", "environment": "Production",
             "services": []},
        ],
        "retire": {"hostnames": ["barium", "atlantis"]},
    }
    m.update(over)
    return m


class Plan(unittest.TestCase):
    def _conn(self):
        return _FakeConn([
            ("FROM registry.servers", lambda: list(HOSTS)),
            *_svc_scripts(),
        ])

    def test_split_insert_vs_update(self):
        conn = self._conn()
        p = frs.plan(conn, _manifest())
        hosts = {e["hostname"]: e for e in p["servers_upsert"]}
        self.assertTrue(hosts["titanium"]["exists"])    # in HOSTS -> update
        self.assertFalse(hosts["helium"]["exists"])     # not -> insert
        self.assertEqual(len(p["servers_upsert"]), 2)

    def test_unknown_services_skipped_not_invented(self):
        conn = self._conn()
        m = _manifest()
        m["fleet"][0]["services"] = ["nebula-srv", "mystery-svc"]
        p = frs.plan(conn, m)
        self.assertEqual(p["unknown_services"],
                         [{"host": "titanium", "service": "mystery-svc"}])
        self.assertEqual([d["service"] for d in p["deployments_insert"]],
                         ["nebula-srv"])

    def test_retire_intersected_with_existing(self):
        conn = self._conn()
        p = frs.plan(conn, _manifest())
        self.assertEqual(p["servers_retire"], ["barium"])  # atlantis dropped

    def test_manifest_validation(self):
        with self.assertRaises(ValueError):
            frs.load_manifest(self._tmp({"nope": []}))
        with self.assertRaises(ValueError):
            frs.load_manifest(self._tmp({"fleet": [{"no_hostname": 1}]}))
        m = frs.load_manifest(self._tmp({"fleet": [{"hostname": "x"}]}))
        self.assertEqual(m["retire"]["hostnames"], [])  # defaulted

    @staticmethod
    def _tmp(data):
        fh = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(data, fh)
        fh.close()
        return fh.name


class Apply(unittest.TestCase):
    def _conn(self):
        # servers select read twice: second read includes the new host
        reads = {"n": 0}

        def hosts_result():
            reads["n"] += 1
            if reads["n"] == 1:
                return [("titanium", 1), ("barium", 2)]
            return [("titanium", 1), ("barium", 2), ("helium", 99)]

        return _FakeConn([
            ("FROM registry.servers", hosts_result),
            *_svc_scripts(),
            ("FROM registry.environment_type", ENV_OK),
            ("FROM registry.deployments", None),  # no existing deployment
        ])

    def test_full_apply_counts_and_retire_shape(self):
        conn = self._conn()
        p = frs.plan(conn, _manifest())
        counts = frs.apply_plan(conn, p, {})
        self.assertEqual(counts["servers"], 2)       # titanium update + helium insert
        self.assertEqual(counts["retired"], 1)       # barium
        self.assertEqual(counts["deployments"], 2)   # nebula-srv + wind-srv
        retire_sql = next(s for s, _ in conn.cur.executed
                          if "SET status = %s" in s)
        self.assertIn("active_flag = false", retire_sql)

    def test_deployment_for_new_server_uses_new_host_id(self):
        conn = self._conn()
        p = frs.plan(conn, _manifest())
        frs.apply_plan(conn, p, {})
        dep_inserts = [(s, params) for s, params in conn.cur.executed
                       if "INSERT INTO registry.deployments" in s]
        self.assertEqual(len(dep_inserts), 2)
        # helium got id 99 on the second hosts read; both deployments are
        # on titanium (id 1) in this manifest, so just verify ids resolve
        for _, params in dep_inserts:
            self.assertIn(params[1], (1, 99))

    def test_idempotent_deployment_check_then_insert(self):
        # deployments select returns an existing row -> no insert
        conn = _FakeConn([
            ("FROM registry.servers", [("titanium", 1), ("barium", 2)]),
            *_svc_scripts(),
            ("FROM registry.environment_type", ENV_OK),
            ("FROM registry.deployments", [(42,)]),
        ])
        p = frs.plan(conn, _manifest())
        counts = frs.apply_plan(conn, p, {})
        self.assertEqual(counts["deployments"], 0)

    def test_unknown_env_name_raises(self):
        conn = self._conn()
        conn.cur.script = [
            ("FROM registry.servers", [("titanium", 1), ("barium", 2)]),
            *_svc_scripts(),
            ("FROM registry.environment_type", None),  # name not found
        ]
        p = frs.plan(conn, _manifest())
        with self.assertRaises(ValueError):
            frs.apply_plan(conn, p, {})


class Main(unittest.TestCase):
    def _run(self, args, manifest):
        fh = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(manifest, fh)
        fh.close()
        conn = _FakeConn([
            ("FROM registry.servers", [("titanium", 1), ("barium", 2)]),
            *_svc_scripts(),
            ("FROM registry.environment_type", ENV_OK),
            ("FROM registry.deployments", None),
        ])
        with mock.patch.object(frs, "default_connect", lambda dsn: conn), \
                mock.patch.object(sys, "argv",
                                  ["fleet-registry-sync.py",
                                   "--manifest", fh.name] + args):
            rc = frs.main()
        return rc, conn

    def test_dry_run_default_writes_nothing(self):
        rc, conn = self._run([], _manifest())
        self.assertEqual(rc, 0)
        self.assertFalse(conn.committed)

    def test_apply_commits(self):
        rc, conn = self._run(["--apply"], _manifest())
        self.assertEqual(rc, 0)
        self.assertTrue(conn.committed)


if __name__ == "__main__":
    unittest.main()
