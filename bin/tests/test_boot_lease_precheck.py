#!/usr/bin/env python3
"""Boot-shim tests for the checkpoint-only lease precheck (LC1–LC7).

`--blackboard-advance-only` is the end-of-turn step (R17.1): it advances the
role's inbox/todo checkpoints and nothing else — no lease issue, no clock-in,
no inbox pull. That shape is correct, but it left a hole: a session could
reach end-of-turn having never run the full boot, advance checkpoints, write
records, raise PRs and post to forums with no lease window covering any of it,
and still get a clean report. Nothing surfaced the gap.

The precheck closes that hole *read-only*: it reads ACTIVE leases and records
`ok` or `degraded`. It must never issue or renew — a lease opened at
end-of-turn would make the census claim coverage the session never had.

Pinned invariants:

  LC1  no ACTIVE lease for the role        -> degraded, names the role@channel
  LC1b role has ACTIVE leases elsewhere    -> degraded, lists those channels
  LC2  ACTIVE lease on this channel        -> ok, carries id + expiry
  LC3  --lease skip                        -> skipped (policy honored)
  LC4  nebula-mcp unreachable              -> degraded, boot survives
  LC5  nebula-mcp-client not importable    -> degraded, boot survives
  LC6  read-only: never calls role_lease_issue / role_lease_renew
  LC7  wired into the advance-only branch ahead of the checkpoint advance,
       and absent from run() (the full boot keeps issuing its own lease)
"""

import contextlib
import importlib.util
import inspect
import io
import json
import os
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "freebuff_boot", os.path.join(_SELF_DIR, "..", "freebuff-boot.py"))
freebuff_boot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freebuff_boot)

Boot = freebuff_boot.Boot


def _lease(lease_id="lease-1", role="dba", channel="interactive", **kw):
    row = {
        "id": lease_id,
        "role": role,
        "channel": channel,
        "model": "freebuff/buffy",
        "status": "ACTIVE",
        "expires_at": "2026-11-02T01:34:23.078Z",
    }
    row.update(kw)
    return row


def _mcp_result(payload):
    """Mimic a Streamable-HTTP MCP tool result (content[].text JSON)."""
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


class _FakeClient:
    """Records every tools/call; returns canned results per tool name."""

    calls = []

    def __init__(self, *a, **kw):
        pass

    @staticmethod
    def _default(tool):
        raise AssertionError(f"unexpected tool call: {tool}")

    def call(self, tool, args=None):
        type(self).calls.append((tool, args))
        # Class-level lookup: the handlers are plain functions on the patched
        # subclass, so they take (args) — not the bound (self, args).
        handler = getattr(type(self), "on_" + tool, None)
        return handler(args) if handler else self._default(tool)


def _install(**handlers):
    """Patch nebula_mcp_client.McpClient for the duration of the test."""
    fake = type("PatchedClient", (_FakeClient,), dict(handlers))
    fake.calls = []
    return fake


def _boot(**kw):
    defaults = dict(role="dba", model="freebuff/buffy", channel="interactive",
                    ttl=14400, budget=100, lease_policy="auto",
                    update_pointer=False, limit=5, dry_run=False, strict=False)
    defaults.update(kw)
    return Boot(**defaults)


def _step(boot, name):
    steps = [s for s in boot.steps if s["step"] == name]
    assert len(steps) == 1, f"expected one {name} step, got {len(steps)}"
    return steps[0]


class TestLeasePrecheck(unittest.TestCase):
    def test_lc1_no_lease_degrades(self):
        """LC1: no ACTIVE lease for the role -> degraded, actionable detail."""
        fake = _install(**{"on_role_lease_status": lambda a: _mcp_result({"items": []})})
        with mock.patch.object(freebuff_boot, "McpClient", fake):
            b = _boot()
            with contextlib.redirect_stdout(io.StringIO()):
                b.lease_precheck()
        step = _step(b, "lease-precheck")
        self.assertEqual(step["status"], "degraded")
        self.assertIn("no ACTIVE lease for dba@interactive", step["detail"])
        self.assertIn("no ACTIVE lease for dba anywhere", step["detail"])
        self.assertIn("freebuff-boot.py --role dba", step["detail"])

    def test_lc1b_lease_on_another_channel_degrades(self):
        """LC1b: a lease on a different channel is not coverage for this one."""
        fake = _install(**{"on_role_lease_status": lambda a: _mcp_result(
            {"items": [_lease(channel="opencode")]})})
        with mock.patch.object(freebuff_boot, "McpClient", fake):
            b = _boot()
            with contextlib.redirect_stdout(io.StringIO()):
                b.lease_precheck()
        step = _step(b, "lease-precheck")
        self.assertEqual(step["status"], "degraded")
        self.assertIn("opencode only", step["detail"])

    def test_lc2_active_lease_ok(self):
        """LC2: a lease on this channel -> ok, with id and expiry."""
        fake = _install(**{"on_role_lease_status": lambda a: _mcp_result(
            {"items": [_lease(lease_id="abc-123")]})})
        with mock.patch.object(freebuff_boot, "McpClient", fake):
            b = _boot()
            with contextlib.redirect_stdout(io.StringIO()):
                b.lease_precheck()
        step = _step(b, "lease-precheck")
        self.assertEqual(step["status"], "ok")
        self.assertIn("abc-123", step["detail"])
        self.assertIn("2026-11-02T01:34:23.078Z", step["detail"])

    def test_lc2b_channel_defaults_when_lease_omits_it(self):
        """A lease row without a channel counts for the caller's channel."""
        fake = _install(**{"on_role_lease_status": lambda a: _mcp_result(
            {"items": [_lease(channel=None)]})})
        with mock.patch.object(freebuff_boot, "McpClient", fake):
            b = _boot()
            with contextlib.redirect_stdout(io.StringIO()):
                b.lease_precheck()
        self.assertEqual(_step(b, "lease-precheck")["status"], "ok")

    def test_lc3_lease_skip_policy_honored(self):
        """LC3: --lease skip -> skipped; no MCP call is made."""
        fake = _install(**{"on_role_lease_status": lambda a: _mcp_result({"items": []})})
        with mock.patch.object(freebuff_boot, "McpClient", fake):
            b = _boot(lease_policy="skip")
            with contextlib.redirect_stdout(io.StringIO()):
                b.lease_precheck()
        self.assertEqual(_step(b, "lease-precheck")["status"], "skipped")
        self.assertEqual(fake.calls, [])

    def test_lc4_nebula_unreachable_degrades(self):
        """LC4: transport failure -> degraded; the turn continues."""

        def _boom(args):
            raise ConnectionError("nebula-mcp down")

        fake = _install(**{"on_role_lease_status": _boom})
        with mock.patch.object(freebuff_boot, "McpClient", fake):
            b = _boot()
            with contextlib.redirect_stdout(io.StringIO()):
                b.lease_precheck()
        step = _step(b, "lease-precheck")
        self.assertEqual(step["status"], "degraded")
        self.assertIn("ConnectionError", step["detail"])

    def test_lc5_client_library_absent_degrades(self):
        """LC5: unimportable client library -> degraded, never an exception."""
        with mock.patch.object(freebuff_boot, "McpClient", None):
            b = _boot()
            with contextlib.redirect_stdout(io.StringIO()):
                b.lease_precheck()
        step = _step(b, "lease-precheck")
        self.assertEqual(step["status"], "degraded")
        self.assertIn("not importable", step["detail"])

    def test_lc6_read_only_never_writes(self):
        """LC6: the precheck must never issue or renew a lease."""
        fake = _install(**{"on_role_lease_status": lambda a: _mcp_result({"items": []})})
        with mock.patch.object(freebuff_boot, "McpClient", fake):
            b = _boot()
            with contextlib.redirect_stdout(io.StringIO()):
                b.lease_precheck()
        tools = [t for t, _ in fake.calls]
        self.assertEqual(tools, ["role_lease_status"])
        for write_tool in ("role_lease_issue", "role_lease_renew"):
            self.assertNotIn(write_tool, tools)


class TestWiring(unittest.TestCase):
    def test_lc7_advance_only_runs_precheck_first(self):
        """LC7: precheck precedes the checkpoint advance in the advance-only branch."""
        src = inspect.getsource(freebuff_boot.main)
        branch = src[src.index("if args.blackboard_advance_only:"):
                     src.index("code = boot.run()")]
        self.assertIn("boot.lease_precheck()", branch)
        self.assertIn("boot.blackboard_advance_step()", branch)
        self.assertLess(branch.index("boot.lease_precheck()"),
                        branch.index("boot.blackboard_advance_step()"))

    def test_lc7b_full_boot_does_not_use_precheck(self):
        """The full boot issues/renews via lease(); precheck is the gap reporter."""
        self.assertNotIn("self.lease_precheck()", inspect.getsource(Boot.run))

    def test_lc7c_advance_only_still_skips_the_full_boot(self):
        """No regression on BA4: advance-only returns before boot.run()."""
        src = inspect.getsource(freebuff_boot.main)
        branch = src[src.index("if args.blackboard_advance_only:"):
                     src.index("code = boot.run()")]
        self.assertIn("return boot.report()", branch)
        self.assertNotIn("boot.run()", branch)


if __name__ == "__main__":
    unittest.main()
