#!/usr/bin/env python3
"""Hermetic tests for the boot shim's --digest step (continuity thread 65fe85a8).

No services are contacted by the digest-step tests themselves: the continuity
package is injected via sys.modules (or removed to simulate PR #274 not being
merged), and digest assembly is stubbed. The one main()-level plumbing test
stubs the Boot class, so no network happens at all.

Covers:
  flag plumbing      — --digest reaches Boot(want_digest=True); default False
  step: off          — no --digest → no "digest" step recorded
  step: ok           — package present + assembly works → step ok + JSON on stdout
  step: pkg absent   — ImportError → step skipped with the PR-#274 hint
  step: source error — _live_fetchers raising → step degraded, boot continues
  step: dry-run      — zero-mutation stance honored → step skipped
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

_spec = importlib.util.spec_from_file_location(
    "freebuff_boot", os.path.join(_SELF_DIR, "..", "freebuff-boot.py"))
freebuff_boot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freebuff_boot)


def _install_fake_continuity(assemble=None, fetchers=None):
    """Inject a fake continuity.digest module; returns a cleanup callable."""
    pkg = types.ModuleType("continuity")
    pkg.__path__ = []  # mark as package
    mod = types.ModuleType("continuity.digest")
    mod._live_fetchers = fetchers or (lambda role: (lambda: [], lambda: [], lambda: [], "level <= 4"))
    mod.assemble_digest = assemble or (lambda role, model, fi, ft, fr, ceiling: {
        "digest_version": "v0", "disposition": "context-only", "role": role,
        "assembled_for_model": model, "as_of": "2026-09-16T00:00:00+00:00",
        "level_provenance": {"level_filter_allowed": ceiling,
                             "applied_at": "source-query"},
        "open_inbox": fi(), "open_threads": ft(), "recent_records_metadata": fr(),
        "sources_degraded": [], "counts": {"open_inbox": 0, "open_threads": 0,
                                           "recent_records": 0},
    })
    sys.modules["continuity"] = pkg
    sys.modules["continuity.digest"] = mod

    def cleanup():
        sys.modules.pop("continuity", None)
        sys.modules.pop("continuity.digest", None)
    return cleanup


class TestFlagPlumbing(unittest.TestCase):
    def test_digest_flag_reaches_boot(self):
        captured = {}

        class StubBoot:
            def __init__(self, **kw):
                captured.update(kw)
                self.steps = []

            def run(self):
                return 0

        with mock.patch.object(freebuff_boot, "Boot", StubBoot):
            rc = freebuff_boot.main(["--role", "dba", "--model", "m", "--digest"])
        self.assertEqual(rc, 0)
        self.assertTrue(captured.get("want_digest"))

    def test_default_is_off(self):
        captured = {}

        class StubBoot:
            def __init__(self, **kw):
                captured.update(kw)
                self.steps = []

            def run(self):
                return 0

        with mock.patch.object(freebuff_boot, "Boot", StubBoot):
            freebuff_boot.main(["--role", "dba", "--model", "m"])
        self.assertFalse(captured.get("want_digest"))


class TestDigestStep(unittest.TestCase):
    def _boot(self, want=True, dry_run=False):
        return freebuff_boot.Boot(role="dba", model="freebuff/buffy",
                                  channel="interactive", ttl=3600, budget=10,
                                  lease_policy="auto", update_pointer=False,
                                  limit=10, dry_run=dry_run, strict=False,
                                  want_digest=want)

    def test_no_flag_no_step(self):
        boot = self._boot(want=False)
        boot.digest_preview()
        self.assertNotIn("digest", [s["step"] for s in boot.steps])

    def test_ok_step_prints_digest_json(self):
        cleanup = _install_fake_continuity()
        self.addCleanup(cleanup)
        boot = self._boot()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            boot.digest_preview()
        step = [s for s in boot.steps if s["step"] == "digest"][0]
        self.assertEqual(step["status"], "ok")
        self.assertIn("disposition=context-only", step["detail"])
        # the JSON block starts at the first '{' printed after the step line
        out = buf.getvalue()
        payload = json.loads(out[out.index("{"):])
        self.assertEqual(payload["disposition"], "context-only")
        self.assertEqual(payload["role"], "dba")
        self.assertEqual(payload["assembled_for_model"], "freebuff/buffy")

    def test_package_absent_is_skip_with_hint(self):
        cleanup = _install_fake_continuity()
        cleanup()  # ensure absent
        boot = self._boot()
        boot.digest_preview()
        step = [s for s in boot.steps if s["step"] == "digest"][0]
        self.assertEqual(step["status"], "skipped")
        self.assertIn("#274", step["detail"])

    def test_source_error_is_degraded_boot_continues(self):
        def boom(role):
            raise RuntimeError("nebula down")
        cleanup = _install_fake_continuity(fetchers=boom)
        self.addCleanup(cleanup)
        boot = self._boot()
        boot.digest_preview()
        step = [s for s in boot.steps if s["step"] == "digest"][0]
        self.assertEqual(step["status"], "degraded")
        self.assertIn("nebula down", step["detail"])

    def test_dry_run_skips(self):
        cleanup = _install_fake_continuity()
        self.addCleanup(cleanup)
        boot = self._boot(dry_run=True)
        boot.digest_preview()
        step = [s for s in boot.steps if s["step"] == "digest"][0]
        self.assertEqual(step["status"], "skipped")

    def test_run_sequence_places_digest_after_lease_block(self):
        """Digest step runs after lease/inbox, before clock_in (adoption-gated)."""
        boot = self._boot()
        calls = []
        with mock.patch.object(boot, "preflight", return_value={"nebula-mcp": False}), \
             mock.patch.object(boot, "clock_in", side_effect=lambda: calls.append("clock")), \
             mock.patch.object(boot, "forums", side_effect=lambda: calls.append("forums")), \
             mock.patch.object(boot, "procedures", side_effect=lambda: calls.append("procs")), \
             mock.patch.object(boot, "report", return_value=0), \
             mock.patch.object(boot, "digest_preview",
                               side_effect=lambda: calls.append("digest")):
            boot.run()
        self.assertEqual(calls, ["digest", "clock", "forums", "procs"])


if __name__ == "__main__":
    unittest.main()
