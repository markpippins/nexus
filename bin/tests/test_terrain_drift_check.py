"""Tests for bin/terrain-drift-check.py (offline, stubbed probes)."""
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
    def _install(probe, services, mcps=None, servers=SERVERS):
        mcps = mcps or []

        def fake_get_json(url):
            if "runnable-services" in url:
                return {"data": services}
            if "mcp-servers" in url:
                return {"data": mcps}
            return {"data": servers}

        monkeypatch.setattr(mod, "get_json", fake_get_json)
        monkeypatch.setattr(mod, "tcp_open", probe)
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
    assert mod.tcp_open("127.0.0.1", 4222) is True  # local nats is up
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
    _, _, _, checked, _, defects = mod.census()
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
    _, _, _, _, _, defects = mod.census()
    assert defects[0]["class"] == "probe-config"


def test_inactive_and_portless_skipped(patch_census):
    probe = StubProbe({})
    patch_census(services=[
        svc(1, "retired", 1234, active=False),
        svc(2, "worker", None),
        svc(3, "offlined", 1234, status="OFFLINE"),
    ], probe=probe)
    _, _, _, checked, skipped, defects = mod.census()
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
    _, _, _, checked, _, defects = mod.census()
    assert checked == 1
    assert defects[0]["class"] == "dead"


def test_helium_hcu_uses_ip_not_hostname(patch_census):
    """Regression: helium rows must probe the LAN IP, not the hostname."""
    probe = StubProbe({"192.168.1.229:11434": True})
    patch_census(
        services=[svc(1, "helium-ollama", 11434,
                      hcu="http://192.168.1.229:11434/")],
        probe=probe)
    _, _, _, checked, _, defects = mod.census()
    assert checked == 1
    assert probe.calls == [("192.168.1.229", 11434)]
    assert defects == []
