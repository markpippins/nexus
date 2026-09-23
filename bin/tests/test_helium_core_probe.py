import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROBE_PATH = HERE.parent / "helium_core_probe.py"
spec = importlib.util.spec_from_file_location("helium_core_probe", PROBE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ACTUATOR_URL = "http://helium:8092/actuator/health"
VERSION_URL = "http://helium:11434/api/version"
TAGS_URL = "http://helium:11434/api/tags"


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


def ok_actuator(disk_free_gb=23.1):
    return {
        "status": "UP",
        "components": {
            "db": {"status": "UP"},
            "diskSpace": {"status": "UP",
                          "details": {"free": disk_free_gb * 1024 ** 3}},
        },
    }


def ok_fetcher():
    return StubFetcher({
        ACTUATOR_URL: ok_actuator(),
        VERSION_URL: {"version": "0.34.1"},
        TAGS_URL: {"models": [{"name": "nomic-embed-text:latest"}]},
    })


def all_running():
    def _fetch(host, timeout):
        return {n: "running" for n in mod.DEFAULT_CONTAINERS}
    return _fetch


def make_args(**kw):
    defaults = dict(
        host="helium", timeout=1.0, min_disk_free_gb=2.0,
        containers="nexus-core,helium-mongo,helium-redis,helium-nats",
        state_file=Path("/tmp/probe-test-state.json"), dry_run=True,
        no_alert=False, no_actuator=False,
    )
    defaults.update(kw)
    return type("Args", (), defaults)()


def alert_recorder(posted):
    def _post(body, tag, names, overall, dry_run):
        posted.append((tag, body, tuple(names)))
        return True
    return _post


# --------------------------------------------------------------------------
# ssh leg: IPv4 pinning + dedicated timeout (2026-09-21 flap fix, salvaged
# from the helium-probe-ipv4 worktree triage — implementation was already on
# main via #441's landing; these regression tests were not)
# --------------------------------------------------------------------------

def test_fetch_docker_states_forces_ipv4():
    """ssh must pin -4: stale AAAA from helium's reimage made the v6-first
    path flap the probe incident/recovery for a day (root-cause record
    0d822504)."""
    calls = []
    real_run = mod.subprocess.run

    def fake_run(cmd, capture_output, text, timeout):
        calls.append((list(cmd), timeout))
        return type("R", (), {"stdout": "nexus-core\trunning", "returncode": 0})()

    mod.subprocess.run = fake_run
    try:
        mod.fetch_docker_states("helium", 9.0)
    finally:
        mod.subprocess.run = real_run
    cmd, timeout = calls[0]
    assert "-4" in cmd, f"ssh command must force IPv4, got: {cmd}"
    assert "-o" in cmd and "BatchMode=yes" in cmd
    assert timeout == 9.0


def test_check_containers_uses_dedicated_ssh_timeout():
    seen = {}

    def states(host, timeout):
        seen["args"] = (host, timeout)
        return {n: "running" for n in mod.DEFAULT_CONTAINERS}

    mod.check_containers(states, "helium", 1.0, list(mod.DEFAULT_CONTAINERS),
                         ssh_timeout=7.5)
    assert seen["args"] == ("helium", 7.5)


def test_check_containers_default_ssh_timeout_is_ten_seconds():
    seen = {}

    def states(host, timeout):
        seen["timeout"] = timeout
        return {n: "running" for n in mod.DEFAULT_CONTAINERS}

    mod.check_containers(states, "helium", 1.0, list(mod.DEFAULT_CONTAINERS))
    assert seen["timeout"] == mod.DEFAULT_SSH_TIMEOUT
    assert mod.DEFAULT_SSH_TIMEOUT == 10.0


def test_run_probe_passes_ssh_timeout_to_container_check():
    seen = {}

    def states(host, timeout):
        seen["timeout"] = timeout
        return {n: "running" for n in mod.DEFAULT_CONTAINERS}

    sf = Path("/tmp/probe-test-ssh-timeout-state.json")
    mod.run_probe(make_args(state_file=sf, ssh_timeout=6.5),
                  fetch=ok_fetcher(), fetch_states=states,
                  poster=alert_recorder([]))
    assert seen["timeout"] == 6.5


# --------------------------------------------------------------------------
# check_actuator
# --------------------------------------------------------------------------

def test_actuator_ok_reports_disk_free():
    fetch = StubFetcher({ACTUATOR_URL: ok_actuator()})
    r = mod.check_actuator(fetch, ACTUATOR_URL, 1.0, 2.0)
    assert r["status"] == "ok"
    assert "disk_free=23.1GB" in r["detail"]


def test_actuator_down_is_fail():
    fetch = StubFetcher({ACTUATOR_URL: {"status": "DOWN"}})
    r = mod.check_actuator(fetch, ACTUATOR_URL, 1.0, 2.0)
    assert r["status"] == "fail"


def test_actuator_unreachable_is_fail():
    r = mod.check_actuator(StubFetcher({}), ACTUATOR_URL, 1.0, 2.0)
    assert r["status"] == "fail"
    assert "unreachable" in r["detail"]


def test_actuator_db_component_down_is_warn():
    body = ok_actuator()
    body["components"]["db"] = {"status": "DOWN"}
    fetch = StubFetcher({ACTUATOR_URL: body})
    r = mod.check_actuator(fetch, ACTUATOR_URL, 1.0, 2.0)
    assert r["status"] == "warn"
    assert "db=DOWN" in r["detail"]


def test_actuator_low_disk_is_warn():
    fetch = StubFetcher({ACTUATOR_URL: ok_actuator(disk_free_gb=1.2)})
    r = mod.check_actuator(fetch, ACTUATOR_URL, 1.0, 2.0)
    assert r["status"] == "warn"
    assert "disk_free" in r["detail"]


def test_actuator_missing_components_is_warn():
    fetch = StubFetcher({ACTUATOR_URL: {"status": "UP", "components": {}}})
    r = mod.check_actuator(fetch, ACTUATOR_URL, 1.0, 2.0)
    assert r["status"] == "warn"


# --------------------------------------------------------------------------
# check_containers
# --------------------------------------------------------------------------

def running_states(**overrides):
    states = {n: "running" for n in mod.DEFAULT_CONTAINERS}
    # allow python-friendly kwargs like nexus_core="exited" for "nexus-core"
    states.update({k.replace("_", "-"): v for k, v in overrides.items()})
    return states


def states_stub(mapping):
    def _fetch(host, timeout):
        if isinstance(mapping, Exception):
            raise mapping
        return mapping
    return _fetch


def test_containers_all_running():
    rs = mod.check_containers(states_stub(running_states()), "helium", 1.0,
                              mod.DEFAULT_CONTAINERS)
    assert len(rs) == 4
    assert all(r["status"] == "ok" for r in rs)


def test_container_missing_from_docker_ps_is_fail():
    states = running_states()
    del states["helium-mongo"]
    rs = mod.check_containers(states_stub(states), "helium", 1.0,
                              mod.DEFAULT_CONTAINERS)
    assert rs[1]["status"] == "fail"
    assert "not listed" in rs[1]["detail"]


def test_container_exited_is_fail():
    rs = mod.check_containers(
        states_stub(running_states(helium_mongo="exited")), "helium", 1.0,
        mod.DEFAULT_CONTAINERS)
    assert rs[1]["status"] == "fail"
    assert rs[1]["detail"] == "state=exited"


def test_container_ssh_failure_fails_all():
    rs = mod.check_containers(states_stub(ConnectionError("no route")),
                              "helium", 1.0, mod.DEFAULT_CONTAINERS)
    assert all(r["status"] == "fail" for r in rs)


# --------------------------------------------------------------------------
# check_ollama
# --------------------------------------------------------------------------

def test_ollama_ok_with_models():
    fetch = StubFetcher({VERSION_URL: {"version": "0.34.1"},
                         TAGS_URL: {"models": [{"name": "nomic-embed-text:latest"}]}})
    r = mod.check_ollama(fetch, "http://helium:11434", 1.0)
    assert r["status"] == "ok"
    assert "nomic-embed-text" in r["detail"]


def test_ollama_no_models_is_warn():
    fetch = StubFetcher({VERSION_URL: {"version": "0.34.1"},
                         TAGS_URL: {"models": []}})
    r = mod.check_ollama(fetch, "http://helium:11434", 1.0)
    assert r["status"] == "warn"


def test_ollama_tags_failure_still_warns():
    fetch = StubFetcher({VERSION_URL: {"version": "0.34.1"}})
    r = mod.check_ollama(fetch, "http://helium:11434", 1.0)
    assert r["status"] == "warn"


def test_ollama_down_is_fail():
    r = mod.check_ollama(StubFetcher({}), "http://helium:11434", 1.0)
    assert r["status"] == "fail"


# --------------------------------------------------------------------------
# aggregation + transitions
# --------------------------------------------------------------------------

def test_aggregate_fail_beats_warn_beats_ok():
    assert mod.aggregate([{"status": "ok"}, {"status": "warn"}]) == "warn"
    assert mod.aggregate([{"status": "ok"}, {"status": "fail"}]) == "fail"
    assert mod.aggregate([{"status": "ok"}]) == "ok"


def test_strongest_tag_ordering():
    assert mod.strongest_tag(["type:warning", "type:incident"]) == "type:incident"
    assert mod.strongest_tag(["type:recovery", "type:warning"]) == "type:recovery"
    assert mod.strongest_tag(["type:warning"]) == "type:warning"


# --------------------------------------------------------------------------
# run_probe end-to-end with stubs
# --------------------------------------------------------------------------

def test_no_actuator_skips_the_retired_tier_but_keeps_the_infra(tmp_path):
    """Retirement shape (2026-09-22): the JVM tier is stopped, helium-mongo/
    redis/nats + ollama stay watched. With --no-actuator the dead :8092 target
    must not appear in the check set at all, and the run must come back ok."""
    sf = tmp_path / "state.json"
    posted = []
    fetch = StubFetcher({VERSION_URL: {"version": "0.34.1"},
                         TAGS_URL: {"models": [{"name": "nomic-embed-text"}]}})
    rc = mod.run_probe(
        make_args(state_file=sf, no_actuator=True,
                  containers="helium-mongo,helium-redis,helium-nats"),
        fetch=fetch, fetch_states=all_running(), poster=alert_recorder(posted))
    assert rc == 0
    assert posted == []
    state = json.loads(sf.read_text())
    names = [c["name"] for c in state["checks"]]
    assert "actuator" not in names                 # retired tier not probed
    assert "container:nexus-core" not in names      # nor its container
    assert names == ["container:helium-mongo", "container:helium-redis",
                     "container:helium-nats", "ollama"]
    assert state["overall"] == "ok"
    assert ACTUATOR_URL not in fetch.calls          # never even dialled


def test_first_run_healthy_no_alert_but_state_saved(tmp_path):
    sf = tmp_path / "state.json"
    posted = []
    rc = mod.run_probe(make_args(state_file=sf), fetch=ok_fetcher(),
                       fetch_states=all_running(),
                       poster=alert_recorder(posted))
    assert rc == 0
    assert posted == []                       # baseline run: silent when healthy
    state = json.loads(sf.read_text())
    assert state["overall"] == "ok"


def test_first_run_already_failing_alerts(tmp_path):
    sf = tmp_path / "state.json"
    posted = []
    rc = mod.run_probe(make_args(state_file=sf), fetch=StubFetcher({}),
                       fetch_states=all_running(),
                       poster=alert_recorder(posted))
    assert rc == 1
    assert [t for t, _b, _n in posted] == ["type:incident"]


def test_ok_to_fail_posts_incident_once(tmp_path):
    sf = tmp_path / "state.json"
    mod.run_probe(make_args(state_file=sf), fetch=ok_fetcher(),
                  fetch_states=all_running(), poster=alert_recorder([]))
    # break the stack
    bad_fetch = StubFetcher({ACTUATOR_URL: {"status": "DOWN"},
                             VERSION_URL: {"version": "0.34.1"},
                             TAGS_URL: {"models": [{"name": "m"}]}})
    posted = []
    rc = mod.run_probe(make_args(state_file=sf), fetch=bad_fetch,
                       fetch_states=states_stub(running_states(nexus_core="exited")),
                       poster=alert_recorder(posted))
    assert rc == 1
    tags = [t for t, _b, _n in posted]
    assert tags == ["type:incident"]
    # run again unchanged: no duplicate alert
    posted.clear()
    mod.run_probe(make_args(state_file=sf), fetch=bad_fetch,
                  fetch_states=states_stub(running_states(nexus_core="exited")),
                  poster=alert_recorder(posted))
    assert posted == []


def test_fail_to_ok_posts_recovery(tmp_path):
    sf = tmp_path / "state.json"
    bad_fetch = StubFetcher({ACTUATOR_URL: {"status": "DOWN"},
                             VERSION_URL: {"version": "0.34.1"},
                             TAGS_URL: {"models": [{"name": "m"}]}})
    mod.run_probe(make_args(state_file=sf), fetch=bad_fetch,
                  fetch_states=all_running(), poster=alert_recorder([]))
    posted = []
    rc = mod.run_probe(make_args(state_file=sf), fetch=ok_fetcher(),
                       fetch_states=all_running(),
                       poster=alert_recorder(posted))
    assert rc == 0
    assert [t for t, _b, _n in posted] == ["type:recovery"]


def test_ok_to_warn_posts_warning(tmp_path):
    sf = tmp_path / "state.json"
    mod.run_probe(make_args(state_file=sf), fetch=ok_fetcher(),
                  fetch_states=all_running(), poster=alert_recorder([]))
    low_disk = ok_actuator(disk_free_gb=1.0)
    warn_fetch = StubFetcher({ACTUATOR_URL: low_disk,
                              VERSION_URL: {"version": "0.34.1"},
                              TAGS_URL: {"models": [{"name": "m"}]}})
    posted = []
    rc = mod.run_probe(make_args(state_file=sf), fetch=warn_fetch,
                       fetch_states=all_running(),
                       poster=alert_recorder(posted))
    assert rc == 0
    assert [t for t, _b, _n in posted] == ["type:warning"]


def test_no_alert_flag_skips_posting_and_state(tmp_path):
    sf = tmp_path / "state.json"
    posted = []
    rc = mod.run_probe(make_args(state_file=sf, no_alert=True),
                       fetch=StubFetcher({}), fetch_states=all_running(),
                       poster=alert_recorder(posted))
    assert rc == 1
    assert posted == []
    assert not sf.exists()


def test_alert_body_lists_all_changed_checks(tmp_path):
    sf = tmp_path / "state.json"
    mod.run_probe(make_args(state_file=sf), fetch=ok_fetcher(),
                  fetch_states=all_running(), poster=alert_recorder([]))
    bad_fetch = StubFetcher({ACTUATOR_URL: {"status": "DOWN"},
                             VERSION_URL: {"version": "0.34.1"},
                             TAGS_URL: {"models": [{"name": "m"}]}})
    posted = []
    mod.run_probe(make_args(state_file=sf), fetch=bad_fetch,
                  fetch_states=states_stub(running_states(helium_redis="exited",
                                                          helium_nats="exited")),
                  poster=alert_recorder(posted))
    assert len(posted) == 1
    body = posted[0][1]
    assert "actuator: ok -> fail" in body
    assert "container:helium-redis: ok -> fail" in body
    assert "container:helium-nats: ok -> fail" in body


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_fail_path_exit_and_json(tmp_path, capsys):
    state_file = tmp_path / "state.json"
    rc = mod.main(["--host", "invalid.invalid", "--timeout", "0.2",
                   "--state-file", str(state_file), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 1
    parsed = json.loads(out)
    assert parsed["overall"] == "fail"
    assert parsed["first_run"] is True
    assert state_file.exists()


def test_cli_bad_flag_is_usage_error():
    with pytest.raises(SystemExit) as ei:
        mod.main(["--not-a-flag"])
    assert ei.value.code == 2
