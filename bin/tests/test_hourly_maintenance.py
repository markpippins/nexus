#!/usr/bin/env python3
"""Guards the two defects incident 8adcac4d filed against the hourly cycle.

1. It created a NEW syslog thread every hour, attributed to the engineer user,
   instead of appending to the persistent heartbeat thread as sysadmin.
2. It exited 1 whenever any issue was seen — including a zombie whose parent
   was still alive, which this script has no way to reap — so the timer unit
   was red every hour by construction and the failure signal became noise.

Every test monkeypatches the probes, so nothing here touches a service, a
process table, or the network.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "bin" / "hourly_maintenance.py"
ENGINEER_UUID = "af069ff6-760c-44cb-a0d4-11517164169b"

_spec = importlib.util.spec_from_file_location("hourly_maintenance", SCRIPT)
hourly = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hourly)


class TestHeartbeatTarget(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig_post = hourly._post

        def fake_post(url, body, timeout=15):
            self.calls.append((url, body))
            return 201, {"id": "posted"}

        hourly._post = fake_post

    def tearDown(self):
        hourly._post = self._orig_post

    def test_appends_a_comment_on_the_persistent_thread(self):
        tid = hourly.post_syslog("# cycle")
        self.assertEqual(tid, hourly._HEARTBEAT_THREAD)
        self.assertEqual(len(self.calls), 1)
        url, body = self.calls[0]
        self.assertEqual(
            url,
            f"{hourly.ASSEMBLY}/api/forums/threads/{hourly._HEARTBEAT_THREAD}/comments",
        )
        self.assertNotIn(
            "/forums/syslog/threads",
            url,
            "a cycle must not create a new syslog thread",
        )
        self.assertEqual(body["role"], "sysadmin")
        self.assertEqual(body["postedById"], hourly._SYSADMIN_UUID)

    def test_does_not_post_as_the_engineer_user(self):
        hourly.post_syslog("# cycle")
        _, body = self.calls[0]
        self.assertNotEqual(
            body["postedById"],
            ENGINEER_UUID,
            "sysadmin maintenance output must not be filed as engineer work",
        )

    def test_failed_post_returns_none(self):
        hourly._post = lambda *a, **k: (500, {"error": "boom"})
        self.assertIsNone(hourly.post_syslog("# cycle"))


class TestExitSemantics(unittest.TestCase):
    def _run(self, issues, zombies):
        saved = (hourly.service_status, hourly.zombie_processes,
                 hourly.disk_space, hourly._post)
        hourly.service_status = lambda: issues
        hourly.zombie_processes = lambda: zombies
        hourly.disk_space = lambda: "disk: ok"
        hourly._post = lambda *a, **k: (201, {"id": "posted"})
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = hourly.main()
        finally:
            (hourly.service_status, hourly.zombie_processes,
             hourly.disk_space, hourly._post) = saved
        return rc, buf.getvalue()

    def test_unreapable_zombie_does_not_fail_the_unit(self):
        rc, out = self._run([], ["zombie: PID 88640 xdg-open"])
        self.assertEqual(
            rc, 0, "a zombie with a live parent cannot be reaped here; it must not fail the unit"
        )
        self.assertIn("xdg-open", out, "the zombie must still be reported")

    def test_service_issue_fails_the_unit(self):
        rc, _ = self._run(["nebula-srv DOWN"], [])
        self.assertEqual(rc, 1)

    def test_all_clear_passes(self):
        rc, out = self._run([], [])
        self.assertEqual(rc, 0)
        self.assertIn("All clear", out)

    def test_missing_heartbeat_post_is_visible(self):
        saved_post = hourly._post
        saved = (hourly.service_status, hourly.zombie_processes, hourly.disk_space)
        hourly.service_status = lambda: []
        hourly.zombie_processes = lambda: []
        hourly.disk_space = lambda: "disk: ok"
        hourly._post = lambda *a, **k: (500, {"error": "boom"})
        err = io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                rc = hourly.main()
        finally:
            hourly._post = saved_post
            (hourly.service_status, hourly.zombie_processes, hourly.disk_space) = saved
        self.assertEqual(rc, 0, "a post failure must not fail the cycle")
        self.assertIn("heartbeat not posted", err.getvalue())


if __name__ == "__main__":
    unittest.main()
