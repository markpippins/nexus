"""Hermetic tests for bin/endpoint-register.py (fleet slice 2).

No DB, no network: connect/probe seams are injected fakes. Pins the
R1 0d40c061 contract:

  - identity: host/instance resolution, hostname-encoding of instance
    (UNIQUE(unit, instance) must not collide across hosts)
  - probe-gated status: UP only on successful probe; DOWN is data
  - register upserts (conflict path) and sets unit explicitly
  - seed retirement: 'primary'/no-heartbeat rows only, scoped correctly
  - heartbeat: re-asserts liveness without topology mutation
  - manifest loading: validation, tcp flag, error on missing fields
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
_TOOL = os.path.join(_REPO, "bin", "endpoint-register.py")

_spec = importlib.util.spec_from_file_location("endpoint_register", _TOOL)
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)


class _FakeResult:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append(("exec", sql, params))


class _FakeConn:
    def __init__(self):
        self.log = []
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return _FakeCursor(self.log)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _mk_args(**kw):
    ns = mock.Mock()
    ns.dsn = "postgresql://fake"
    ns.host = None
    ns.instance = None
    ns.service = "nebula-srv"
    ns.port = 3101
    ns.scheme = "http"
    ns.health_path = "/"
    ns.manifest = None
    ns.no_retire = False
    ns.all = False
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class Identity(unittest.TestCase):
    def test_host_env_then_gethostname(self):
        with mock.patch.dict(os.environ, {"NEXUS_HOST": "titanium"}, clear=True):
            self.assertEqual(er.resolve_host(None), "titanium")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(er.resolve_host(None))  # gethostname fallback

    def test_instance_hostname_encoded_not_primary(self):
        with mock.patch.dict(os.environ, {"NEXUS_HOST": "helium"}, clear=True):
            self.assertEqual(er.resolve_instance(None, "helium"), "helium")
        with mock.patch.dict(os.environ, {"NEXUS_INSTANCE": "ti-2"}, clear=True):
            self.assertEqual(er.resolve_instance(None, "titanium"), "ti-2")

    def test_explicit_wins(self):
        self.assertEqual(er.resolve_host("entropy"), "entropy")
        self.assertEqual(er.resolve_instance("c-1", "helium"), "c-1")


class SeedRetirement(unittest.TestCase):
    def test_retire_sql_is_scoped_to_seed_rows(self):
        cur = _FakeCursor([])
        cur.execute = mock.Mock(return_value=None)
        cur.rowcount = 3
        n = er.retire_seeds(cur, None)
        self.assertEqual(n, 3)
        sql = cur.execute.call_args[0][0]
        self.assertIn("instance = %(seed)s", sql)
        self.assertIn("last_heartbeat IS NULL", sql)
        self.assertIn("status <> 'RETIRED'", sql)

    def test_retire_with_service_scopes_to_unit(self):
        cur = _FakeCursor([])
        cur.execute = mock.Mock(return_value=None)
        cur.rowcount = 1
        er.retire_seeds(cur, "cascade")
        params = cur.execute.call_args[0][1]
        self.assertEqual(params, {"seed": "primary", "service": "cascade"})


class Register(unittest.TestCase):
    def test_row_id_is_deterministic_per_host_instance_service(self):
        a = er.endpoint_id("titanium", "titanium", "nebula-srv")
        b = er.endpoint_id("titanium", "titanium", "nebula-srv")
        c = er.endpoint_id("titanium", "titanium", "wind-srv")
        d = er.endpoint_id("helium", "helium", "nebula-srv")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, d)
        self.assertRegex(a, r"^[0-9a-f-]{36}$")

    def test_upsert_carries_explicit_id(self):
        # live catch: terrain.service_endpoints.id is NOT NULL with no
        # default — the INSERT must supply it (deterministic uuid5).
        self.assertIn("(id, host, instance", er.UPSERT_SQL.replace("\n", " "))
        self.assertIn("%(id)s", er.UPSERT_SQL)

    def test_upsert_supplies_ip_and_unit(self):
        # live catch #2: ip (inet) and unit are NOT NULL on live; the
        # conflict target (unit, instance) requires unit in the VALUES.
        flat = er.UPSERT_SQL.replace("\n", " ")
        self.assertIn("ip, unit, port", flat)
        self.assertIn("%(ip)s", flat)
        self.assertIn("%(unit)s", flat)
        self.assertIn("ip              = EXCLUDED.ip", flat)

    def test_resolve_ip_manifest_override_wins(self):
        self.assertEqual(er.resolve_ip("192.168.1.50"), "192.168.1.50")

    def test_resolve_ip_env_then_loopback_fallback(self):
        with mock.patch.dict(os.environ, {"NEXUS_IP": "10.0.0.9"}, clear=True):
            self.assertEqual(er.resolve_ip(None), "10.0.0.9")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(er.socket.socket, "connect",
                                   side_effect=OSError("no route")):
                self.assertEqual(er.resolve_ip(None), "127.0.0.1")

    def test_resolve_ip_never_reports_loopback_from_egress(self):
        # the egress trick on a host with only the 127.0.1.1 /etc/hosts
        # alias must NOT record loopback as fleet-routable
        class _Sock:
            def getsockname(self):
                return ("127.0.1.1", 0)

            def close(self):
                pass

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(er.socket, "socket", return_value=_Sock()):
                self.assertEqual(er.resolve_ip(None), "127.0.0.1")

    def test_register_passes_resolved_ip_and_unit(self):
        conn = _FakeConn()
        captured = {}
        real_upsert = er.upsert_endpoint

        def spy(cur, **kw):
            captured.update(kw)
            return real_upsert(cur, **kw)

        with mock.patch.dict(os.environ, {"NEXUS_IP": "192.168.8.8"}, clear=True):
            with mock.patch.object(er, "upsert_endpoint", spy):
                rc = er.cmd_register(_mk_args(), lambda dsn: conn,
                                     lambda t: (True, "ok"))
        self.assertEqual(rc, 0)
        self.assertEqual(captured["ip"], "192.168.8.8")
        self.assertEqual(captured["service"], "nebula-srv")

    def test_up_ok_on_successful_probe(self):
        conn = _FakeConn()
        with mock.patch.dict(os.environ, {"NEXUS_HOST": "titanium"}, clear=True):
            rc = er.cmd_register(
                _mk_args(no_retire=True), lambda dsn: conn,
                lambda target: (True, "probe-ok"),
            )
        self.assertEqual(rc, 0)
        self.assertTrue(conn.committed)
        execs = [e for e in conn.log if e[0] == "exec"]
        self.assertTrue(any("INSERT INTO" in e[1] for e in execs))
        upsert = next(e for e in execs if "ON CONFLICT" in e[1])
        self.assertEqual(upsert[2]["status"], "UP")

    def test_down_is_data_on_failed_probe(self):
        conn = _FakeConn()
        rc = er.cmd_register(
            _mk_args(no_retire=True), lambda dsn: conn,
            lambda target: (False, "probe-failed rc=7"),
        )
        self.assertEqual(rc, 0)
        upsert = next(e for e in conn.log if e[0] == "exec" and "ON CONFLICT" in e[1])
        self.assertEqual(upsert[2]["status"], "DOWN")

    def test_tcp_manifest_entry_probes_port(self):
        conn = _FakeConn()
        seen = []

        def fake_probe(target):
            seen.append(target)
            return (True, "tcp-ok")

        rc = er.cmd_register(
            _mk_args(no_retire=True, service=None, manifest=self._tcp_manifest()),
            lambda dsn: conn, fake_probe,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [("tcp", 3100)])

    @staticmethod
    def _tcp_manifest():
        fh = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(
            {"services": [{"service": "conduit-mcp", "port": 3100, "tcp": True}]},
            fh,
        )
        fh.close()
        return fh.name

    def test_manifest_missing_fields_rejected(self):
        fh = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"services": [{"service": "x"}]}, fh)  # no port
        fh.close()
        with self.assertRaises(ValueError):
            er.load_manifest(fh.name)

    def test_db_error_rolls_back(self):
        conn = _FakeConn()
        conn.cursor = mock.Mock(side_effect=RuntimeError("db down"))
        rc = er.cmd_register(_mk_args(), lambda dsn: conn, lambda t: (True, "ok"))
        self.assertEqual(rc, 1)
        self.assertTrue(conn.rolled_back)

    def test_needs_service_or_manifest(self):
        rc = er.cmd_register(
            _mk_args(service=None, port=None), lambda dsn: _FakeConn(),
            lambda t: (True, "ok"),
        )
        self.assertEqual(rc, 1)


class Heartbeat(unittest.TestCase):
    def test_reasserts_liveness_without_topology_change(self):
        conn = _FakeConn()
        with mock.patch.dict(os.environ, {"NEXUS_HOST": "titanium"}, clear=True):
            rc = er.cmd_heartbeat(
                _mk_args(manifest=self._manifest()), lambda dsn: conn
            )
        self.assertEqual(rc, 0)
        self.assertTrue(conn.committed)
        execs = [e for e in conn.log if e[0] == "exec"]
        self.assertEqual(len(execs), 1)
        self.assertIn("last_heartbeat = now()", execs[0][1])
        self.assertIn("host = %s", execs[0][1])
        self.assertEqual(execs[0][2][2], ["nebula-srv", "assembly-srv"])

    @staticmethod
    def _manifest():
        fh = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(
            {"services": [
                {"service": "nebula-srv", "port": 3101},
                {"service": "assembly-srv", "port": 3107},
            ]},
            fh,
        )
        fh.close()
        return fh.name


if __name__ == "__main__":
    unittest.main()
