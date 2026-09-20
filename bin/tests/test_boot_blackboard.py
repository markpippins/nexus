#!/usr/bin/env python3
"""Boot-shim tests for the --blackboard step (V192 blackboard digest, slice 2).

Mirrors test_boot_conn_record.py conventions: the shim is loaded by file
path, a fake continuity.blackboard module is injected into sys.modules, and
every step disposition is pinned. The digest must never fail the boot.

Pins the wiring contract (thread 88385a46 slice 2):

  B1  flag off -> step absent (blackboard_step returns without recording)
  B2  --blackboard renders: format printed, digest JSON printed, [ok] line
      carries the cache disposition
  B3  inert view (status "inert-skip") -> [skipped], boot continues
  B4  render failure (status "error") -> [degraded], boot continues
  B5  module import failure -> [skipped] (absence is a skip, not an error)
  B6  render raises -> [degraded] (never fails the boot)
  B7  --dry-run -> [skipped] with zero-mutation detail; render never called
  B8  --blackboard-advance threads advance=True through to the module
  B9  default advance is False (render-only; checkpoint advance is explicit)
  B10 run() invokes the step between consolidate and forums (order pinned)
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

Boot = freebuff_boot.Boot

_OK_RESULT = {
    "status": "ok",
    "cache": "miss",
    "format": "blackboard [dba] action-needed=1 in-flight=2 (cache miss)",
    "digest": {"role": "dba", "todo": {"buckets": {}}, "inbox": {"counts": {}}},
}


def _install_fake_blackboard(result=None, render_error=None,
                             import_error=None):
    """Inject a fake continuity.blackboard module; returns cleanup callable."""
    captured = {"render_kwargs": None}

    pkg = types.ModuleType("continuity")
    pkg.__path__ = []  # mark as package (preserves any real submodules)
    mod = types.ModuleType("continuity.blackboard")

    if import_error is not None:
        # Simulate module-absence honestly: create the module object but
        # attach NONE of the imported names, so the shim's
        # `from continuity.blackboard import ...` itself raises ImportError
        # and lands on the [skipped] import-failure path.
        pass
    elif render_error is not None:
        def _raise_render(*a, **k):
            raise render_error
        mod.render_role_digest = _raise_render
        mod.RedisCache = lambda **k: object()
    else:
        class _FakeCache:
            def __init__(self, **kw):
                captured["cache_kwargs"] = kw

        def _render(role, dsn, cache=None, advance=False, model=None):
            captured["render_kwargs"] = {
                "role": role, "dsn": dsn, "cache": cache,
                "advance": advance, "model": model}
            return result or dict(_OK_RESULT)

        mod.render_role_digest = _render
        mod.RedisCache = _FakeCache

    sys.modules["continuity"] = pkg
    sys.modules["continuity.blackboard"] = mod
    mod._captured = captured

    def cleanup():
        sys.modules.pop("continuity.blackboard", None)
        sys.modules.pop("continuity", None)
    return cleanup


def _boot(**kw):
    defaults = dict(role="dba", model="freebuff/buffy", channel="interactive",
                    ttl=14400, budget=100, lease_policy="auto",
                    update_pointer=False, limit=5, dry_run=False, strict=False)
    defaults.update(kw)
    return Boot(**defaults)


def _steps(boot):
    return {s["step"]: s for s in boot.steps}


class TestFlagPlumbing(unittest.TestCase):
    def test_b1_flag_off_step_absent(self):
        """B1: without --blackboard the step is absent entirely."""
        cleanup = _install_fake_blackboard()
        try:
            b = _boot()
            with contextlib.redirect_stdout(io.StringIO()):
                b.blackboard_step()
            self.assertEqual([s for s in b.steps if s["step"] == "blackboard"],
                             [])
        finally:
            cleanup()

    def test_b2_render_ok(self):
        """B2: --blackboard prints format + digest JSON, records [ok]."""
        cleanup = _install_fake_blackboard()
        try:
            b = _boot(want_blackboard=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                b.blackboard_step()
            step = _steps(b)["blackboard"]
            self.assertEqual(step["status"], "ok")
            self.assertIn("cache miss", step["detail"])
            self.assertIn("action-needed=1", out.getvalue())
            self.assertIn('"role": "dba"', out.getvalue())
            kw = sys.modules["continuity.blackboard"]._captured[
                "render_kwargs"]
            self.assertEqual(kw["role"], "dba")
            self.assertFalse(kw["advance"])  # B9 default: render-only
        finally:
            cleanup()

    def test_b3_inert_skip(self):
        """B3: inert view -> [skipped], boot continues."""
        result = {"status": "inert-skip",
                  "reason": "v_coordination_blackboard absent (V192 not applied)"}
        cleanup = _install_fake_blackboard(result=result)
        try:
            b = _boot(want_blackboard=True)
            with contextlib.redirect_stdout(io.StringIO()):
                b.blackboard_step()
            step = _steps(b)["blackboard"]
            self.assertEqual(step["status"], "skipped")
            self.assertIn("V192", step["detail"])
        finally:
            cleanup()

    def test_b4_render_error_degrades(self):
        """B4: status error -> [degraded], boot continues."""
        result = {"status": "error", "reason": "PG down"}
        cleanup = _install_fake_blackboard(result=result)
        try:
            b = _boot(want_blackboard=True)
            with contextlib.redirect_stdout(io.StringIO()):
                b.blackboard_step()
            self.assertEqual(_steps(b)["blackboard"]["status"], "degraded")
        finally:
            cleanup()

    def test_b5_module_absent_skips(self):
        """B5: unimportable module -> [skipped] (absence is a skip)."""
        cleanup = _install_fake_blackboard(import_error=ImportError("nope"))
        try:
            b = _boot(want_blackboard=True)
            with contextlib.redirect_stdout(io.StringIO()):
                b.blackboard_step()
            step = _steps(b)["blackboard"]
            self.assertEqual(step["status"], "skipped")
            self.assertIn("not importable", step["detail"])
        finally:
            cleanup()

    def test_b6_render_raises_degrades(self):
        """B6: render exception -> [degraded]; the boot must never fail."""
        cleanup = _install_fake_blackboard(render_error=RuntimeError("boom"))
        try:
            b = _boot(want_blackboard=True)
            with contextlib.redirect_stdout(io.StringIO()):
                b.blackboard_step()
            step = _steps(b)["blackboard"]
            self.assertEqual(step["status"], "degraded")
            self.assertIn("RuntimeError", step["detail"])
        finally:
            cleanup()

    def test_b7_dry_run_zero_mutation(self):
        """B7: --dry-run -> [skipped]; the module is never invoked."""
        cleanup = _install_fake_blackboard()
        try:
            b = _boot(want_blackboard=True, dry_run=True)
            with contextlib.redirect_stdout(io.StringIO()):
                b.blackboard_step()
            step = _steps(b)["blackboard"]
            self.assertEqual(step["status"], "skipped")
            self.assertIn("dry-run", step["detail"])
            self.assertIn("zero-mutation", step["detail"])
            self.assertIsNone(
                sys.modules["continuity.blackboard"]._captured[
                    "render_kwargs"])
        finally:
            cleanup()

    def test_b8_advance_threads_through(self):
        """B8: --blackboard-advance passes advance=True to the module."""
        cleanup = _install_fake_blackboard()
        try:
            b = _boot(want_blackboard=True, blackboard_advance=True)
            with contextlib.redirect_stdout(io.StringIO()):
                b.blackboard_step()
            kw = sys.modules["continuity.blackboard"]._captured[
                "render_kwargs"]
            self.assertTrue(kw["advance"])
            step = _steps(b)["blackboard"]
            self.assertIn("checkpoints advanced", step["detail"])
        finally:
            cleanup()

    def test_b10_run_order_pinned(self):
        """B10: the step runs between consolidate_step and forums."""
        b = _boot()
        names = [n for n in
                 ("consolidate_step", "blackboard_step", "forums")
                 for n in [n] if hasattr(Boot, n)]
        # structural: the step methods exist in Boot's run order via source
        import inspect
        src = inspect.getsource(Boot.run)
        i1 = src.index("self.consolidate_step()")
        i2 = src.index("self.blackboard_step()")
        i3 = src.index("self.forums()")
        self.assertTrue(i1 < i2 < i3)
        self.assertEqual(len(names), 3)  # all three hooks exist


class TestArgParsing(unittest.TestCase):
    """Flag plumbing through main() (StubBoot convention from test_boot_digest)."""

    def _main_captures(self, *argv):
        captured = {}

        class StubBoot:
            def __init__(self, **kw):
                captured.update(kw)
                self.steps = []

            def run(self):
                return 0

        with mock.patch.object(freebuff_boot, "Boot", StubBoot):
            rc = freebuff_boot.main(list(argv))
        return rc, captured

    def test_flags_default_off(self):
        rc, captured = self._main_captures("--role", "dba")
        self.assertEqual(rc, 0)
        self.assertFalse(captured.get("want_blackboard"))
        self.assertFalse(captured.get("blackboard_advance"))

    def test_flags_parse_and_reach_boot(self):
        rc, captured = self._main_captures(
            "--role", "dba", "--blackboard", "--blackboard-advance")
        self.assertEqual(rc, 0)
        self.assertTrue(captured.get("want_blackboard"))
        self.assertTrue(captured.get("blackboard_advance"))


class TestRunOrderWiring(unittest.TestCase):
    """B10: the step is wired into run() between consolidate and forums."""

    def test_run_order_pinned(self):
        import inspect
        src = inspect.getsource(Boot.run)
        i1 = src.index("self.consolidate_step()")
        i2 = src.index("self.blackboard_step()")
        i3 = src.index("self.forums()")
        self.assertTrue(i1 < i2 < i3)


if __name__ == "__main__":
    unittest.main()
