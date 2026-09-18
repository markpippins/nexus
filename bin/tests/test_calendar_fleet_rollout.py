#!/usr/bin/env python3
"""Hermetic tests for bin/calendar-fleet-rollout.py.

No network, no ssh: run_remote is replaced with a scripted fake that answers
each command with canned (rc, stdout, stderr). Pins the pipeline contract
from R1 4f4aaab1 and the lessons of the three manual rollouts:

  - audit refuses root-owned artifacts (admin-notes 746e6169) and dirty trees
  - remote auto-detect: origin preferred; URL-match fallback (helium's `github`)
  - install verifies installed state, not exit code (helium lesson)
  - probe provisioning is idempotent and verifies enablement
  - verify demands a machine-attributed event GAINED, not just a trigger rc=0
  - linger is reported, never forced
  - dry-run touches nothing; exit code reflects host failures
"""

import importlib.util
import json
import os
import sys
import unittest.mock

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
TOOL = os.path.join(_REPO, "bin", "calendar-fleet-rollout.py")

spec = importlib.util.spec_from_file_location("fleetroll", TOOL)
roll = importlib.util.module_from_spec(spec)
sys.modules["fleetroll"] = roll  # dataclasses resolves __module__ at exec time
spec.loader.exec_module(roll)

OK = 0


class FakeRemote:
    """Scripted ssh responder with event-count state.

    Ordered matchers (first hit wins); stateful replies for the verify-step
    queries so before/after counts can differ around the trigger:
      - 'systemctl --user start <x>-health.service' → trigger: rc 0, and
        events grows by grow_delta when grow_on_trigger
      - '"machine":"' (the attribution grep) → machine_count
      - 'events.jsonl' (the total count) → current events
    Everything else falls to the ordered script, then the catch-all.
    """

    def __init__(self, script=None, events=0, machine_count=1, grow_on_trigger=True):
        self.script = list(script or [])
        self.events = events
        self.machine_count = machine_count
        self.grow_on_trigger = grow_on_trigger
        self.calls = []

    def __call__(self, host, cmd, timeout=60):
        self.calls.append((host, cmd))
        if "systemctl --user start" in cmd and "-health.service" in cmd:
            if self.grow_on_trigger:
                self.events += 1
            return OK, "", ""
        if "calendar-emit.py emit" in cmd:  # the no-spec verification emit
            if self.grow_on_trigger:
                self.events += 1
            return OK, "", ""
        if "grep -c" in cmd and "calendar.jsonl" in cmd:
            # machine-attribution grep (robust to the emitter's serialization
            # spacing — pin the query SHAPE, not the exact quoting)
            return OK, f"{self.machine_count}\n", ""
        if "calendar.jsonl" in cmd:
            return OK, f"{self.events}\n", ""
        for matcher, rc, out in self.script:
            if matcher in cmd:
                return rc, out, ""
        return OK, "0\n", ""  # benign catch-all: unlisted queries count 0


def fake_with(**overrides):
    """Happy-path script for one fresh host with an origin remote.
    Overrides are PREPENDED (shadow the defaults)."""
    script = [
        ("echo ok", OK, "ok\n"),
        ("is-inside-work-tree", OK, "true\n0\n0\n0\n"),
        ("git remote -v", OK, "origin\tsomeone@example.com:x.git (fetch)\n"),
        ("rev-parse --short", OK, "abc1234\n0\n"),
        ("git pull", OK, "Fast-forward\n  a.py | 1 +\n"),
        ("list-unit-files 'calendar-emit@*'", OK, "calendar-emit@.service 1 static\n"),
        ("is-enabled", OK, "enabled\n"),
        ("calendar-emit-on-success.conf", OK, "0\n"),
        ("loginctl show-user", OK, "Linger=yes\n"),
    ]
    return FakeRemote(overrides.get("script", []) + script)


def rollout(host="h", spec=None):
    with unittest.mock.patch.object(roll, "run_remote", CURRENT["fake"]):
        return roll.rollout_host(host, spec)


CURRENT = {"fake": None}  # lets rollout() read the test's fake


class TestHappyPath(unittest.TestCase):
    def setUp(self):
        CURRENT["fake"] = fake_with()

    def test_full_rollout_new_host(self):
        res = rollout("newbox", {"kind": "ollama", "url": "http://localhost:11434/api/version"})
        names = [s.name for s in res.steps]
        self.assertEqual(names, ["reach", "remote", "audit", "sync", "install",
                                 "probe", "verify", "linger"])
        self.assertTrue(res.ok, res.as_dict())
        statuses = {s.name: s.status for s in res.steps}
        self.assertEqual(statuses["sync"], "ok")
        self.assertEqual(statuses["install"], "ok")
        self.assertEqual(statuses["probe"], "ok")
        self.assertEqual(statuses["verify"], "ok")
        self.assertEqual(statuses["linger"], "ok")
        # probe units written before verify triggers the service
        calls = CURRENT["fake"].calls
        probe_idx = next(i for i, c in enumerate(calls) if "OnSuccess=calendar-emit@" in c[1])
        verify_idx = next(i for i, c in enumerate(calls) if "systemctl --user start" in c[1])
        self.assertLess(probe_idx, verify_idx)

    def test_current_host_is_clean_noop(self):
        CURRENT["fake"] = fake_with(script=[
            ("git pull", OK, "Already up to date.\n"),
            ("calendar-emit-on-success.conf", OK, "3\n"),
        ])
        res = rollout("titanium", None)
        statuses = {s.name: s.status for s in res.steps}
        self.assertTrue(res.ok)
        self.assertEqual(statuses["sync"], "skip")
        self.assertEqual(statuses["probe"], "skip")
        self.assertEqual(statuses["install"], "ok")


class TestAuditGates(unittest.TestCase):
    def test_root_owned_artifacts_refuse_pull(self):
        CURRENT["fake"] = fake_with(script=[
            ("is-inside-work-tree", OK,
             "true\n0\n12\n0\nTRACKED docker/vanadium-ci/casc/jenkins.yaml\n"),
        ])
        res = rollout("vanadium", None)
        statuses = {s.name: s.status for s in res.steps}
        self.assertFalse(res.ok)
        self.assertEqual(statuses["audit-root-owned"], "fail")
        self.assertNotIn("sync", statuses)  # never pulled

    def test_root_owned_untracked_is_warning_not_refusal(self):
        """vanadium finding: runtime dirs (docker logs) are root-owned but
        untracked — checkout never touches them, so they cannot wedge a pull.
        Audit must pass with a warning, not refuse."""
        CURRENT["fake"] = fake_with(script=[
            ("is-inside-work-tree", OK,
             "true\n0\n12\n0\nTRACKED /x/jenkins.yaml\n"),
        ])
        res = rollout("vanadium", None)
        self.assertFalse(res.ok)  # tracked one still refuses

        CURRENT["fake"] = fake_with(script=[
            ("is-inside-work-tree", OK,
             "true\n0\n12\n0\nuntracked /home/x/docker/work/logs\n"),
        ])
        res = rollout("vanadium", None)
        self.assertTrue(res.ok, res.as_dict())  # warn only
        a = next(s for s in res.steps if s.name == "audit")
        self.assertIn("root-owned-untracked=1 (warn only)", a.detail)
        self.assertEqual({s.name for s in res.steps if s.status == "fail"}, set())

    def test_dirty_tree_refuses_pull(self):
        CURRENT["fake"] = fake_with(script=[
            ("is-inside-work-tree", OK, "true\n7\n0\n0\n"),
        ])
        res = rollout("helium", None)
        self.assertFalse(res.ok)
        self.assertIn("dirty", res.steps[-1].detail)
        self.assertNotIn("sync", [s.name for s in res.steps])

    def test_unreachable_host_stops_immediately(self):
        CURRENT["fake"] = FakeRemote(script=[("echo ok", 255, "")])
        res = rollout("darkbox", None)
        self.assertFalse(res.ok)
        self.assertEqual([s.name for s in res.steps], ["reach"])
        self.assertEqual(res.steps[0].status, "fail")
    def test_timeout_treated_as_unreachable(self):
        def boom(host, cmd, timeout=60):
            raise roll.subprocess.TimeoutExpired(cmd=["ssh"], timeout=timeout)
        with unittest.mock.patch.object(roll, "run_remote", boom):
            res = roll.rollout_host("slowbox", None)
        self.assertFalse(res.ok)
        self.assertEqual(res.steps[0].name, "reach")

    def test_no_repo_stops_before_remote(self):
        CURRENT["fake"] = FakeRemote(script=[("echo ok", OK, "ok\n"),
                                             ("is-inside-work-tree", 128, "")])
        res = rollout("bare", None)
        self.assertFalse(res.ok)
        # remote detection runs before audit (it needs the tracking ref) and
        # fails first on a host with no repo/remotes
        self.assertEqual([s.name for s in res.steps], ["reach", "remote"])


class TestRemoteDetect(unittest.TestCase):
    def test_origin_preferred(self):
        CURRENT["fake"] = fake_with(script=[
            ("git remote -v", OK, "github\tgit@github.com:markpippins/nexus.git (fetch)\n"
                                  "origin\tgit@github.com:markpippins/nexus.git (fetch)\n"),
        ])
        res = rollout("h", None)
        self.assertTrue(res.ok)
        r = next(s for s in res.steps if s.name == "remote")
        self.assertEqual(r.detail, "origin")

    def test_url_match_fallback(self):
        CURRENT["fake"] = fake_with(script=[
            ("git remote -v", OK, "github\tgit@github.com:markpippins/nexus.git (fetch)\n"),
        ])
        res = rollout("helium-legacy", None)
        r = next(s for s in res.steps if s.name == "remote")
        self.assertEqual(r.status, "ok")
        self.assertIn("github", r.detail)
        self.assertIn("non-standard", r.detail)

    def test_no_match_refuses(self):
        CURRENT["fake"] = fake_with(script=[
            ("git remote -v", OK, "upstream\tgit@example.com:other.git (fetch)\n"),
        ])
        res = rollout("weird", None)
        self.assertFalse(res.ok)
        self.assertEqual(res.steps[-1].name, "remote")

    def test_pull_failure_is_caught(self):
        CURRENT["fake"] = fake_with(script=[
            ("git pull", 1, "", ),
        ])
        res = rollout("h", None)
        statuses = {s.name: s.status for s in res.steps}
        self.assertEqual(statuses.get("sync"), "fail")
        self.assertFalse(res.ok)


class TestInstallVerification(unittest.TestCase):
    def test_exit_code_lies_detected(self):
        CURRENT["fake"] = fake_with(script=[
            ("list-unit-files 'calendar-emit@*'", OK, ""),  # no unit rows
        ])
        res = rollout("h", None)
        statuses = {s.name: s.status for s in res.steps}
        self.assertEqual(statuses["install"], "fail")
        inst = next(s for s in res.steps if s.name == "install")
        self.assertIn("missing", inst.detail)
        self.assertNotIn("verify", statuses)  # never claim success downstream


class TestProbe(unittest.TestCase):
    def test_existing_probe_skipped(self):
        CURRENT["fake"] = fake_with(script=[
            ("list-unit-files 'ollama-health.timer'", OK, "ollama-health.timer 1 enabled\n"),
        ])
        res = rollout("h", {"kind": "ollama", "url": "http://x"})
        self.assertEqual(next(s for s in res.steps if s.name == "probe").status, "skip")

    def test_wrote_but_not_enabled_is_fail(self):
        CURRENT["fake"] = fake_with(script=[
            ("is-enabled", OK, "disabled\n"),
        ])
        res = rollout("h", {"kind": "ollama", "url": "http://x"})
        p = next(s for s in res.steps if s.name == "probe")
        self.assertEqual(p.status, "fail")
        self.assertIn("exit code lied", p.detail)

    def test_dropin_uses_onsuccess_chain(self):
        CURRENT["fake"] = fake_with()
        res = rollout("h", {"kind": "ollama", "url": "http://x/api/version"})
        self.assertTrue(res.ok)
        writes = [c[1] for c in CURRENT["fake"].calls if "OnSuccess=calendar-emit@" in c[1]]
        self.assertTrue(writes)
        self.assertIn("calendar-emit@ollama-health.timer.service", writes[0])


class TestVerify(unittest.TestCase):
    def test_no_machine_event_is_fail(self):
        # machine_count counts ALL machine-attributed events in the file
        # (real JSONL semantics), not just this run's delta.
        CURRENT["fake"] = FakeRemote(script=fake_with().script, events=5, machine_count=0)
        res = rollout("h", None)
        v = next(s for s in res.steps if s.name == "verify")
        self.assertEqual(v.status, "fail")
        self.assertIn("machine=h", v.detail)

    def test_growth_plus_machine_attribution_passes(self):
        CURRENT["fake"] = FakeRemote(script=fake_with().script, events=5, machine_count=6)
        res = rollout("h", None)
        v = next(s for s in res.steps if s.name == "verify")
        self.assertEqual(v.status, "ok")
        self.assertEqual(v.detail, "events 5→6, machine-attributed=6")

    def test_no_growth_without_trigger_growth_is_fail(self):
        CURRENT["fake"] = FakeRemote(script=fake_with().script, events=5,
                                     machine_count=1, grow_on_trigger=False)
        res = rollout("h", None)
        v = next(s for s in res.steps if s.name == "verify")
        self.assertEqual(v.status, "fail")
        self.assertIn("before=5 after=5", v.detail)


class TestLingerReported(unittest.TestCase):
    def test_linger_never_forced(self):
        CURRENT["fake"] = fake_with(script=[("loginctl show-user", OK, "Linger=no\n")])
        res = rollout("h", None)
        l = next(s for s in res.steps if s.name == "linger")
        self.assertEqual(l.status, "skip")
        self.assertIn("enable-linger", l.detail)
        # and no command ever attempted to change it
        self.assertFalse(any("enable-linger" in c[1] for c in CURRENT["fake"].calls))

    def test_linger_enabled_is_ok(self):
        CURRENT["fake"] = fake_with(script=[("loginctl show-user", OK, "Linger=yes\n")])
        res = rollout("h", None)
        l = next(s for s in res.steps if s.name == "linger")
        self.assertEqual(l.status, "ok")


class TestCli(unittest.TestCase):
    def test_parse_probe_specs(self):
        specs = roll.parse_probe_specs(["a:ollama:http://x:11434/api/version",
                                        "b:jenkins:http://b:8080/api/json"])
        self.assertEqual(specs["a"], {"kind": "ollama", "url": "http://x:11434/api/version"})
        self.assertEqual(specs["b"]["url"], "http://b:8080/api/json")

    def test_parse_probe_specs_rejects_malformed(self):
        with self.assertRaises(SystemExit):
            roll.parse_probe_specs(["justhost"])

    def test_dry_run_no_ssh(self):
        calls = []
        with unittest.mock.patch.object(roll, "run_remote",
                                        lambda *a, **k: calls.append(a) or (0, "", "")):
            rc = roll.main(["--hosts", "h1", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_exit_code_reflects_host_failures(self):
        dead = FakeRemote(script=[("echo ok", 255, "")])
        with unittest.mock.patch.object(roll, "run_remote", dead):
            rc = roll.main(["--hosts", "dead", "--report", "/tmp/fr-test.json"])
        self.assertEqual(rc, 1)
        with open("/tmp/fr-test.json") as fh:
            report = json.load(fh)
        self.assertEqual(report["hosts_ok"], 0)
        self.assertEqual(report["results"][0]["host"], "dead")
        os.unlink("/tmp/fr-test.json")

    def test_probe_spec_for_unknown_host_rejected(self):
        with self.assertRaises(SystemExit):
            roll.main(["--hosts", "h1", "--probe-provision", "other:ollama:http://x",
                       "--dry-run"])


if __name__ == "__main__":
    unittest.main()
