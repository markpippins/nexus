import importlib.util
import urllib.error
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROBE_PATH = HERE.parent / "thallium_core_probe.py"
spec = importlib.util.spec_from_file_location("thallium_core_probe", PROBE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BASE = "http://thallium:8092"


# --------------------------------------------------------------------------
# stubs
# --------------------------------------------------------------------------

class StubFetcher:
    """url -> payload or exception."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if url not in self.mapping:
            raise KeyError(f"no stub for {url}")  # simulates unreachable
        payload = self.mapping[url]
        if isinstance(payload, Exception):
            raise payload
        return payload


def http404(url):
    return urllib.error.HTTPError(url, 404, "Not Found", None, None)


def ok_actuator(disk_free_gb=23.1):
    return {
        "status": "UP",
        "components": {
            "db": {"status": "UP"},
            "diskSpace": {"status": "UP",
                          "details": {"free": disk_free_gb * 1024 ** 3}},
        },
    }


def ok_routes_fetcher():
    mapping = {f"{BASE}/actuator/health": ok_actuator()}
    for path in mod.DEFAULT_ROUTES:
        mapping[f"{BASE}{path}"] = {"anything": "goes"}
    return StubFetcher(mapping)


def all_running():
    def _fetch(host, timeout):
        return {n: "running" for n in mod.DEFAULT_CONTAINERS}
    return _fetch


def ok_jetstream():
    def _fetch(host, timeout):
        return {"stream": {"exists": True, "storage": "file", "messages": 5},
                "consumer": {"exists": True, "num_pending": 0,
                             "num_ack_pending": 0}}
    return _fetch


def make_args(**kw):
    defaults = dict(
        host="thallium", timeout=1.0, ssh_timeout=10.0, min_disk_free_gb=2.0,
        containers=",".join(mod.DEFAULT_CONTAINERS),
        routes=",".join(mod.DEFAULT_ROUTES),
        state_file=Path("/tmp/probe-test-state.json"), dry_run=True,
        no_actuator=False, no_routes=False, no_jetstream=False,
        no_alert=False,
    )
    defaults.update(kw)
    return type("Args", (), defaults)()


def alert_recorder(posted):
    def _post(body, tag, names, overall, dry_run):
        posted.append((tag, body, tuple(names)))
        return True
    return _post


# --------------------------------------------------------------------------
# check_route / check_routes
# --------------------------------------------------------------------------

def test_route_200_is_ok():
    fetch = StubFetcher({f"{BASE}/api/meep/health": {}})
    (r,) = mod.check_routes(fetch, "thallium", 1.0, ["/api/meep/health"])
    assert r["status"] == mod.STATUS_OK
    assert r["detail"] == "200"


def test_route_404_is_fail():
    url = f"{BASE}/api/shrapnel/health"
    fetch = StubFetcher({url: http404(url)})
    (r,) = mod.check_routes(fetch, "thallium", 1.0, ["/api/shrapnel/health"])
    assert r["status"] == mod.STATUS_FAIL
    assert "404" in r["detail"]


def test_route_unreachable_is_fail():
    fetch = StubFetcher({})  # KeyError for anything
    (r,) = mod.check_routes(fetch, "thallium", 1.0, ["/api/aegis/registries"])
    assert r["status"] == mod.STATUS_FAIL
    assert "unreachable" in r["detail"]


def test_search_api_quirk_path_is_probed_as_default():
    """/search/api/health (NOT /api/search/health) must be among the default
    routes — the monolith namespaces moleculer-search deliberately."""
    assert "/search/api/health" in mod.DEFAULT_ROUTES
    assert "/api/search/health" not in mod.DEFAULT_ROUTES


def test_routes_use_the_verified_family_set():
    expected = {
        "/api/shrapnel/health", "/api/meep/health", "/api/aegis/registries",
        "/api/solscript/health", "/api/workers/execution/health",
        "/search/api/health",
    }
    assert set(mod.DEFAULT_ROUTES) == expected


# --------------------------------------------------------------------------
# check_actuator (mirrors helium contract)
# --------------------------------------------------------------------------

def test_actuator_ok_reports_disk_free():
    fetch = StubFetcher({f"{BASE}/actuator/health": ok_actuator(disk_free_gb=9.5)})
    r = mod.check_actuator(fetch, f"{BASE}/actuator/health", 1.0, 2.0)
    assert r["status"] == mod.STATUS_OK
    assert "9.5" in r["detail"]


def test_actuator_low_disk_is_warn():
    fetch = StubFetcher({f"{BASE}/actuator/health": ok_actuator(disk_free_gb=1.0)})
    r = mod.check_actuator(fetch, f"{BASE}/actuator/health", 1.0, 2.0)
    assert r["status"] == mod.STATUS_WARN
    assert "disk_free" in r["detail"]


def test_actuator_unreachable_is_fail():
    fetch = StubFetcher({})
    r = mod.check_actuator(fetch, f"{BASE}/actuator/health", 1.0, 2.0)
    assert r["status"] == mod.STATUS_FAIL


# --------------------------------------------------------------------------
# check_containers
# --------------------------------------------------------------------------

def test_containers_all_running():
    results = mod.check_containers(all_running(), "thallium", 1.0,
                                   list(mod.DEFAULT_CONTAINERS))
    assert all(r["status"] == mod.STATUS_OK for r in results)


def test_container_missing_from_docker_ps_is_fail():
    states = {n: "running" for n in mod.DEFAULT_CONTAINERS}
    states.pop("atomic-nats")
    results = mod.check_containers(lambda *a: states, "thallium", 1.0,
                                   list(mod.DEFAULT_CONTAINERS))
    by_name = {r["name"]: r for r in results}
    assert by_name["container:atomic-nats"]["status"] == mod.STATUS_FAIL


# --------------------------------------------------------------------------
# check_jetstream
# --------------------------------------------------------------------------

def test_jetstream_healthy_is_ok():
    r = mod.check_jetstream(ok_jetstream(), "thallium", 1.0)
    assert r["status"] == mod.STATUS_OK
    assert "lag=0" in r["detail"]


def test_jetstream_missing_stream_is_fail():
    def _fetch(host, timeout):
        return {"stream": {"exists": False}, "consumer": {"exists": False}}
    r = mod.check_jetstream(_fetch, "thallium", 1.0)
    assert r["status"] == mod.STATUS_FAIL
    assert "missing" in r["detail"].lower()


def test_jetstream_storage_rendered_both_ways():
    for storage in ("file", "StorageType.FILE"):
        def _fetch(host, timeout, storage=storage):
            return {"stream": {"exists": True, "storage": storage, "messages": 0},
                    "consumer": {"exists": True, "num_pending": 0,
                                 "num_ack_pending": 0}}
        r = mod.check_jetstream(_fetch, "thallium", 1.0)
        assert r["status"] == mod.STATUS_OK, storage


def test_jetstream_memory_storage_is_warn():
    def _fetch(host, timeout):
        return {"stream": {"exists": True, "storage": "memory", "messages": 0},
                "consumer": {"exists": True, "num_pending": 0,
                             "num_ack_pending": 0}}
    r = mod.check_jetstream(_fetch, "thallium", 1.0)
    assert r["status"] == mod.STATUS_WARN
    assert "storage=memory" in r["detail"]


def test_jetstream_missing_consumer_is_warn():
    def _fetch(host, timeout):
        return {"stream": {"exists": True, "storage": "file", "messages": 3},
                "consumer": {"exists": False}}
    r = mod.check_jetstream(_fetch, "thallium", 1.0)
    assert r["status"] == mod.STATUS_WARN
    assert "consumer" in r["detail"]


def test_jetstream_backlog_is_warn():
    def _fetch(host, timeout):
        return {"stream": {"exists": True, "storage": "file", "messages": 500},
                "consumer": {"exists": True, "num_pending": 500,
                             "num_ack_pending": 0}}
    r = mod.check_jetstream(_fetch, "thallium", 1.0)
    assert r["status"] == mod.STATUS_WARN
    assert "num_pending=500" in r["detail"]


def test_jetstream_unreachable_is_fail():
    def _fetch(host, timeout):
        raise ConnectionError("nope")
    r = mod.check_jetstream(_fetch, "thallium", 1.0)
    assert r["status"] == mod.STATUS_FAIL


# --------------------------------------------------------------------------
# aggregation + alerts
# --------------------------------------------------------------------------

def test_aggregate_fail_beats_warn_beats_ok():
    assert mod.aggregate([{"status": mod.STATUS_OK}]) == mod.STATUS_OK
    assert mod.aggregate([{"status": mod.STATUS_OK},
                          {"status": mod.STATUS_WARN}]) == mod.STATUS_WARN
    assert mod.aggregate([{"status": mod.STATUS_WARN},
                          {"status": mod.STATUS_FAIL}]) == mod.STATUS_FAIL


def test_strongest_tag_ordering():
    assert mod.strongest_tag(["type:warning", "type:incident"]) == "type:incident"
    assert mod.strongest_tag(["type:recovery"]) == "type:recovery"


def test_first_run_healthy_no_alert_but_state_saved(tmp_path):
    posted = []
    state = tmp_path / "state.json"
    out = mod.run_probe(make_args(state_file=state, no_jetstream=True),
                        fetch=ok_routes_fetcher(), fetch_states=all_running(),
                        fetch_js=ok_jetstream(), poster=alert_recorder(posted))
    assert out == mod.EXIT_OK
    assert posted == []
    assert state.exists()


def test_first_run_already_failing_alerts(tmp_path):
    posted = []
    fetch = ok_routes_fetcher()
    fetch.mapping[f"{BASE}/api/meep/health"] = http404(f"{BASE}/api/meep/health")
    out = mod.run_probe(make_args(state_file=Path("/tmp/never.json"),
                                  no_jetstream=True),
                        fetch=fetch, fetch_states=all_running(),
                        fetch_js=ok_jetstream(), poster=alert_recorder(posted))
    assert out == mod.EXIT_FAIL
    assert posted and posted[0][0] == "type:incident"
    assert any("route:/api/meep/health" in n for n in posted[0][2])


def test_ok_to_fail_posts_incident_once(tmp_path):
    state = tmp_path / "state.json"
    posted = []
    poster = alert_recorder(posted)

    good = ok_routes_fetcher()
    mod.run_probe(make_args(state_file=state, no_jetstream=True),
                  fetch=good, fetch_states=all_running(),
                  fetch_js=ok_jetstream(), poster=poster)
    assert posted == []

    bad = ok_routes_fetcher()
    bad.mapping[f"{BASE}/search/api/health"] = http404(f"{BASE}/search/api/health")
    mod.run_probe(make_args(state_file=state, no_jetstream=True),
                  fetch=bad, fetch_states=all_running(),
                  fetch_js=ok_jetstream(), poster=poster)
    assert len(posted) == 1
    tag, body, names = posted[0]
    assert tag == "type:incident"
    assert "route:/search/api/health" in names

    # repeat failure — no new alert (transition-only)
    mod.run_probe(make_args(state_file=state, no_jetstream=True),
                  fetch=bad, fetch_states=all_running(),
                  fetch_js=ok_jetstream(), poster=poster)
    assert len(posted) == 1


def test_recovery_posts_recovery(tmp_path):
    state = tmp_path / "state.json"
    posted = []
    poster = alert_recorder(posted)

    bad = ok_routes_fetcher()
    bad.mapping[f"{BASE}/search/api/health"] = http404(f"{BASE}/search/api/health")
    mod.run_probe(make_args(state_file=state, no_jetstream=True),
                  fetch=bad, fetch_states=all_running(),
                  fetch_js=ok_jetstream(), poster=poster)
    good = ok_routes_fetcher()
    mod.run_probe(make_args(state_file=state, no_jetstream=True),
                  fetch=good, fetch_states=all_running(),
                  fetch_js=ok_jetstream(), poster=poster)
    assert posted[-1][0] == "type:recovery"
