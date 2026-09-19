"""Tests for bin/terrain-drift-check.py (offline, stubbed probes).

Pins the OFFLINE-aware drift semantics (#359 census):
  - a host declared OFFLINE in registry.servers (via injected
    load_declared_offline) that probes DOWN is "expected-offline" —
    reported, but NOT a defect and not in the exit-code count
  - a declared-OFFLINE host that probes UP is "stale-offline" —
    the declaration is stale (machine probably powered on)
  - registry unreachable degrades to a note, census still runs
"""
import importlib.util
import json
import socket
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "terrain-drift-check.py"
spec = importlib.util.spec_from_file_location("terrain_drift_check", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

SERVERS = [
    {"id": 1, "hostname": "titanium", "ipAddress": "127.0.0.1"},
    {"id": 3, "hostname": "vanadium", "ipAddress": "192.168.1.209"},
    {"id": 5, "hostname": "helium", "ipAddress": "192.168.1.229"},
]

def svc(id, name, port, status="ONLINE", active=True, hcu=None, serverId=None):
    return {
        "id": id, "name": name, "port": port, "status": status,
        "activeFlag": active, "healthCheckUrl": hcu, "serverId": serverId,
    }


class StubProbe:
    """Replace mod.tcp_open: host:port -> True/False/ValueError."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, host, port):
        self.calls.append((host, port))
        r = self.mapping.get(f"{host}:{port}")
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def patch_census(monkeypatch):
    def _install(probe, services, mcps=None, servers=SERVERS,
                 offline=None, note=None):
        mcps = mcps or []

        def fake_get_json(url):
            if "runnable-services" in url:
                return {"data": services}
            if "mcp-servers" in url:
                return {"data": mcps}
            return {"data": servers}

        def fake_declared_offline(dsn=None):
            return set(offline or ()), note

        monkeypatch.setattr(mod, "get_json", fake_get_json)
        monkeypatch.setattr(mod, "tcp_open", probe)
        monkeypatch.setattr(mod, "load_declared_offline", fake_declared_offline)
    return _install


# --------------------------------------------------------------------------
# probe_target / tcp_open unit behavior
# --------------------------------------------------------------------------

def test_probe_target_prefers_healthcheckurl_host():
    host, prov = mod.probe_target(
        svc(1, "x", 1, hcu="http://192.168.1.229:8092/actuator/health"),
        {1: SERVERS[0]})
    assert (host, prov) == ("192.168.1.229", "healthCheckUrl")


def test_probe_target_falls_back_to_serverid_then_default():
    s_by_id = {s["id"]: s for s in SERVERS}
    assert mod.probe_target(svc(1, "x", 1, serverId=3), s_by_id) == \
        ("192.168.1.209", "serverId")
    assert mod.probe_target(svc(1, "x", 1, serverId=None), s_by_id) == \
        ("localhost", "default(titanium)")


def test_tcp_open_true_false_and_unresolvable():
    # True case: REAL ephemeral loopback listener (hermetic — no external deps)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert mod.tcp_open("127.0.0.1", port) is True
    finally:
        srv.close()
    # False case: a port that was bound (guaranteed free) then closed -> refused
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
        s2.bind(("127.0.0.1", 0))
        closed_port = s2.getsockname()[1]
    assert mod.tcp_open("127.0.0.1", closed_port) is False
    with pytest.raises(ValueError, match="does not resolve"):
        mod.tcp_open("no-such-host.invalid", 80)


def test_tcp_open_rejects_public_ip(monkeypatch):
    def fake_getaddrinfo(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 ("8.8.8.8", port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="PUBLIC"):
        mod.tcp_open("helium", 8092)


# --------------------------------------------------------------------------
# census classification
# --------------------------------------------------------------------------

def test_dead_row_on_own_host(patch_census, monkeypatch):
    probe = StubProbe({
        "localhost:3000": True,
        "localhost:9999": False,   # default(titanium) target, TCP refused -> dead
    })
    patch_census(
        services=[svc(1, "ok", 3000), svc(2, "dead", 9999)],
        probe=probe)
    _, _, _, checked, _, _, defects, _ = mod.census()
    assert checked == 2
    assert probe.calls == [("localhost", 3000), ("localhost", 9999)]
    assert [d["name"] for d in defects] == ["dead"]
    assert defects[0]["class"] == "dead"


def test_probe_config_when_host_unregistered(patch_census):
    # 10.9.9.9 not a registered server address; TCP closes -> probe-config,
    # not "dead" (the registry claim is unverifiable, not provably false)
    probe = StubProbe({"10.9.9.9:80": False})
    patch_census(
        services=[svc(1, "odd-host", 80, hcu="http://10.9.9.9:80/health")],
        probe=probe)
    _, _, _, _, _, _, defects, _ = mod.census()
    assert defects[0]["class"] == "probe-config"


def test_inactive_and_portless_skipped(patch_census):
    probe = StubProbe({})
    patch_census(services=[
        svc(1, "retired", 1234, active=False),
        svc(2, "worker", None),
        svc(3, "offlined", 1234, status="OFFLINE"),
    ], probe=probe)
    _, _, _, checked, skipped, _, defects, _ = mod.census()
    assert checked == 0
    assert len(skipped) == 3
    assert defects == []


def test_mcp_rows_probe_too(patch_census):
    probe = StubProbe({
        "192.168.1.209:9000": False,
    })
    patch_census(services=[], mcps=[
        svc(9, "vd-sonar", 9000, serverId=3),
    ], probe=probe)
    _, _, _, checked, _, _, defects, _ = mod.census()
    assert checked == 1
    assert defects[0]["class"] == "dead"


def test_helium_hcu_uses_ip_not_hostname(patch_census):
    """Regression: helium rows must probe the LAN IP, not the hostname."""
    probe = StubProbe({"192.168.1.229:11434": True})
    patch_census(
        services=[svc(1, "helium-ollama", 11434,
                      hcu="http://192.168.1.229:11434/")],
        probe=probe)
    _, _, _, checked, _, _, defects, _ = mod.census()
    assert checked == 1
    assert probe.calls == [("192.168.1.229", 11434)]
    assert defects == []


# --------------------------------------------------------------------------
# OFFLINE-aware semantics (#359 census)
# --------------------------------------------------------------------------

def test_declared_offline_host_down_is_expected_not_defect(patch_census):
    """A service claiming ONLINE on a declared-OFFLINE host that probes DOWN
    is expected-offline: reported, but NOT a defect (exit-code unaffected)."""
    probe = StubProbe({"192.168.1.212:5432": False})
    patch_census(
        services=[svc(1, "barium-svc", 5432, serverId=3)],
        servers=SERVERS + [
            {"id": 3, "hostname": "barium", "ipAddress": "192.168.1.212"}],
        offline={"barium", "192.168.1.212"},
        probe=probe)
    _, _, _, checked, skipped, expected, defects, note = mod.census()
    assert checked == 1
    assert skipped == []
    assert defects == []                      # not drift
    assert len(expected) == 1
    assert expected[0]["class"] == "expected-offline"
    assert expected[0]["host"] == "192.168.1.212"
    assert note is None


def test_declared_offline_hostname_key_matches_hcu_probe(patch_census):
    """Match keys are exact-lowercase against the probed host: a hostname
    key from the registry matches a probe through an HCU hostname (the
    real loader emits BOTH hostname and IP keys per row, so either
    probe-provisioning path matches)."""
    probe = StubProbe({"barium:5432": False})
    patch_census(
        services=[svc(1, "barium-svc", 5432,
                      hcu="http://barium:5432/")],
        offline={"barium"},                 # hostname key variant
        probe=probe)
    _, _, _, _, _, expected, defects, _ = mod.census()
    assert defects == []
    assert len(expected) == 1
    assert expected[0]["host"] == "barium"


def test_declared_offline_host_up_is_stale_offline_defect(patch_census):
    """A declared-OFFLINE host that probes UP means the declaration is
    stale (machine probably powered on) — this IS a defect."""
    probe = StubProbe({"192.168.1.212:5432": True})
    patch_census(
        services=[svc(1, "barium-svc", 5432, serverId=3)],
        servers=SERVERS + [
            {"id": 3, "hostname": "barium", "ipAddress": "192.168.1.212"}],
        offline={"barium", "192.168.1.212"},
        probe=probe)
    _, _, _, _, _, expected, defects, _ = mod.census()
    assert expected == []
    assert len(defects) == 1
    assert defects[0]["class"] == "stale-offline"
    assert "stale" in defects[0]["detail"]


def test_offline_host_down_stays_dead_when_declaration_absent(patch_census):
    """Without the declared-OFFLINE signal (empty registry set), the same
    down probe is still a plain dead defect — semantics only come from
    the declared layer, never inferred."""
    probe = StubProbe({"192.168.1.212:5432": False})
    patch_census(
        services=[svc(1, "barium-svc", 5432, serverId=3)],
        servers=SERVERS + [
            {"id": 3, "hostname": "barium", "ipAddress": "192.168.1.212"}],
        offline=None,                        # registry reachable, no OFFLINE rows
        probe=probe)
    _, _, _, _, _, expected, defects, _ = mod.census()
    assert expected == []
    assert defects[0]["class"] == "dead"


def test_registry_unreachable_degrades_not_crashes(patch_census):
    """Registry unreachable -> degraded note, empty offline set, census
    still completes and classifies normally."""
    probe = StubProbe({"192.168.1.212:5432": False})
    patch_census(
        services=[svc(1, "barium-svc", 5432, serverId=3)],
        servers=SERVERS + [
            {"id": 3, "hostname": "barium", "ipAddress": "192.168.1.212"}],
        note="registry unreachable (OperationalError) — OFFLINE semantics unavailable",
        probe=probe)
    _, _, _, _, _, expected, defects, note = mod.census()
    assert expected == []
    assert defects[0]["class"] == "dead"     # honest without the declared layer
    assert "unreachable" in note


def test_load_declared_offline_real_db_or_graceful(monkeypatch):
    """Unit: the real loader returns keys on success, ({}, note) on error —
    exercised hermetically via a bad DSN (never raises either way)."""
    keys, note = mod.load_declared_offline(
        "postgresql://pguser:pgpass@localhost:1/nosuchdb")
    assert keys == set()
    assert "unreachable" in note
