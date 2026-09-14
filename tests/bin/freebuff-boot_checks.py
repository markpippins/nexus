#!/usr/bin/env python3
"""Unit tests for bin/freebuff-boot.py (Freebuff-conformance boot shim).

Hermetic: spins up a mock Streamable-HTTP JSON-RPC MCP server (nebula-mcp +
tackle-mcp) and mock REST servers (assembly-srv, timeclock), then points the
shim at them via env overrides. No real services or database are touched.

Pins the behaviors documented in the shim's docstring and the admin-notes
thread "Protocol-portable vs harness-enforced governance":

- --dry-run performs NO mutations: no role_lease_issue/renew, no clock-in,
  no pointer write — while still exercising pre-flight and read-only scans.
- Lease auto policy: renews an ACTIVE lease for the channel, issues a new one
  when none exists; --lease skip touches nothing.
- Degraded mode: a down dependency is reported (step status degraded) and the
  boot continues with exit 0; --strict turns degradation into exit 1.
- Inbox lists records via nebula_get_inbox; --update-pointer advances the
  stored pointer to the newest record's createdAt (ISO).
- Procedure registry is read via tackle-mcp memory_get_procedures.
- Forum scan reads issues-and-open-questions, to-do, and change-log threads.

Usage:
    python3 tests/bin/freebuff-boot_checks.py   # run this suite
    python3 tests/run_all.py bin                # via the repo runner

Exit code: 0 if all pass, 1 otherwise.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

NEXUS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(NEXUS_ROOT, "bin", "freebuff-boot.py")

T2 = 1_752_000_360_000  # newest record createdAt (epoch ms)
REC = {"createdAt": T2, "recordType": "report", "title": "fresh record"}
ACTIVE_LEASE = {"id": "lease-1", "role": "tester", "channel": "interactive", "status": "ACTIVE"}
ISSUED_LEASE = {"id": "lease-2", "role": "tester", "channel": "interactive", "status": "ACTIVE"}


# ── mock MCP server (serves both nebula-mcp and tackle-mcp in tests) ──────
class MockMcp(BaseHTTPRequestHandler):
    """Minimal stateless JSON-RPC MCP server. config/calls are class-level."""
    config = {"active_leases": [], "procedures": {"count": 2, "procedures": [
        {"slug": "inbox-query-procedure", "summary": "s1"},
        {"slug": "tag-routing-reference", "summary": "s2"},
    ]}}
    calls: list[tuple] = []

    def log_message(self, *a):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length).decode())
        except Exception:
            self._send_json({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "parse error"}})
            return
        method = req.get("method", "")
        params = req.get("params") or {}
        if method == "tools/call":
            self.__class__.calls.append((params.get("name", ""), params.get("arguments") or {}))
            result = self._handle_tool(params.get("name", ""), params.get("arguments") or {})
            self._send_json({"jsonrpc": "2.0", "id": req.get("id"), "result": {
                "content": [{"type": "text", "text": json.dumps(result)}]}})
        elif method == "initialize":
            self._send_json({"jsonrpc": "2.0", "id": req.get("id"), "result": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "serverInfo": {"name": "mock", "version": "1"}}})
        else:
            self._send_json({"jsonrpc": "2.0", "id": req.get("id"),
                             "error": {"code": -32601, "message": "method not found"}})

    @classmethod
    def _handle_tool(cls, name, args):
        cfg = cls.config
        if name == "role_lease_status":
            return {"items": cfg.get("active_leases", [])}
        if name == "role_lease_renew":
            return {"ok": True, "id": args.get("id")}
        if name == "role_lease_issue":
            return {"ok": True, "id": "lease-issued"}
        if name == "nebula_get_inbox":
            return {"role": args.get("role"), "pointer": "2026-08-01T00:00:00Z",
                    "items": cfg.get("records", [REC]), "count": 1}
        if name == "nebula_set_inbox_pointer":
            return {"ok": True}
        if name == "memory_get_procedures":
            return cfg.get("procedures", {"count": 2, "procedures": [
                {"slug": "inbox-query-procedure", "summary": "s1"},
                {"slug": "tag-routing-reference", "summary": "s2"},
            ]})
        return {}


# ── mock REST server (assembly-srv forums + timeclock) ────────────────────
class MockRest(BaseHTTPRequestHandler):
    config = {"clock_in_ok": True}
    calls: list[tuple] = []  # (path, method)

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        path = self.path.split("?")[0]
        self.__class__.calls.append((path, method))
        if path == "/clock-in":
            if self.__class__.config["clock_in_ok"]:
                # Real timeclock shape (verified live 2026-09-14): 200 with
                # success flag + record. The shim also accepts {status: ok}.
                self._send({"success": True, "record": {"id": "clock-1", "role": "tester",
                                                       "status": "active"}, "message": "Clocked in"})
            else:
                self._send({"status": "error"}, code=500)
        elif "/forums/" in path and path.endswith("/threads"):
            # Real shim calls are {ASSEMBLY_API}/forums/<slug>/threads...
            slug = path.rstrip("/").split("/")[-2]
            if slug == "issues-and-open-questions":
                self._send({"items": [{"title": "issue: thing is broken"}]})
            elif slug == "to-do":
                self._send({"items": [{"title": "todo: fix the thing"}]})
            elif slug == "change-log":
                self._send({"items": [{"title": "change: did a thing"}]})
            else:
                self._send({"items": []})
        else:
            self._send({}, code=404)

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._route("POST")


# ── harness ───────────────────────────────────────────────────────────────
def start_servers(config=None):
    MockMcp.config = json.loads(json.dumps(config or {})) if config else {
        "active_leases": [], "records": [REC]}
    if "records" not in MockMcp.config:
        MockMcp.config["records"] = [REC]
    MockMcp.calls = []
    MockRest.config = {"clock_in_ok": True}
    MockRest.calls = []
    mcp = HTTPServer(("127.0.0.1", 0), MockMcp)
    rest = HTTPServer(("127.0.0.1", 0), MockRest)
    for s in (mcp, rest):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return mcp, rest


def run_shim(mcp_port, rest_port, args, extra_env=None):
    env = dict(os.environ)
    env["NEBULA_MCP_BASE"] = f"http://127.0.0.1:{mcp_port}"
    env["TACKLE_MCP_BASE"] = f"http://127.0.0.1:{mcp_port}"
    env["ASSEMBLY_API"] = f"http://127.0.0.1:{rest_port}/api"
    env["NEXUS_TIMECLOCK_URL"] = f"http://127.0.0.1:{rest_port}"
    env.update(extra_env or {})
    return subprocess.run([sys.executable, SCRIPT] + args,
                          capture_output=True, text=True, env=env, timeout=60)


def mcp_calls(name):
    return [c for c in MockMcp.calls if c[0] == name]


def rest_calls(path_prefix):
    return [c for c in MockRest.calls if c[0].startswith(path_prefix)]


# ── tests ────────────────────────────────────────────────────────────────
def test_dry_run_issues_no_mutations():
    mcp, rest = start_servers({"active_leases": []})
    try:
        r = run_shim(mcp.server_port, rest.server_port,
                     ["--role", "tester", "--dry-run", "--update-pointer"])
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert mcp_calls("role_lease_issue") == [], "dry-run must not issue leases"
        assert mcp_calls("role_lease_renew") == [], "dry-run must not renew leases"
        assert mcp_calls("nebula_set_inbox_pointer") == [], "dry-run must not write pointer"
        assert rest_calls("/clock-in") == [], "dry-run must not clock in"
        # reads still happen
        assert mcp_calls("nebula_get_inbox"), "dry-run still reads the inbox"
        assert "would issue" in r.stdout, r.stdout
        assert "DEGRADED MODE" not in r.stdout
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_lease_issue_when_no_active_lease():
    mcp, rest = start_servers({"active_leases": []})
    try:
        r = run_shim(mcp.server_port, rest.server_port, ["--role", "tester"])
        assert r.returncode == 0, r.stderr
        issues = mcp_calls("role_lease_issue")
        assert len(issues) == 1, f"expected one lease issue, got {issues}"
        a = issues[0][1]
        assert a["role"] == "tester" and a["channel"] == "interactive"
        assert a["ttlSeconds"] > 0 and a["budgetUnits"] > 0
        assert rest_calls("/clock-in"), "live boot must clock in"
        assert "issued" in r.stdout
        assert "clocked in (clock-1)" in r.stdout, r.stdout
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_lease_renew_when_active_exists():
    mcp, rest = start_servers({"active_leases": [ACTIVE_LEASE]})
    try:
        r = run_shim(mcp.server_port, rest.server_port, ["--role", "tester"])
        assert r.returncode == 0, r.stderr
        assert mcp_calls("role_lease_issue") == [], "must not double-issue with an ACTIVE lease"
        renews = mcp_calls("role_lease_renew")
        assert len(renews) == 1 and renews[0][1]["id"] == "lease-1"
        assert "renewed lease-1" in r.stdout
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_lease_skip_touches_nothing():
    mcp, rest = start_servers({"active_leases": [ACTIVE_LEASE]})
    try:
        r = run_shim(mcp.server_port, rest.server_port, ["--role", "tester", "--lease", "skip"])
        assert r.returncode == 0, r.stderr
        assert mcp_calls("role_lease_issue") == []
        assert mcp_calls("role_lease_renew") == []
        assert "[skip] lease" in r.stdout
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_update_pointer_advances_to_newest():
    mcp, rest = start_servers({"active_leases": [], "records": [REC]})
    try:
        r = run_shim(mcp.server_port, rest.server_port,
                     ["--role", "tester", "--update-pointer"])
        assert r.returncode == 0, r.stderr
        writes = mcp_calls("nebula_set_inbox_pointer")
        assert len(writes) == 1, f"expected one pointer write, got {writes}"
        assert writes[0][1]["timestamp"] == "2025-07-08T18:46:00Z", writes
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_degraded_mode_reported_and_exit_zero():
    # Point nebula-mcp at the REST server (no MCP there) to simulate downtime.
    mcp, rest = start_servers({"active_leases": []})
    try:
        r = run_shim(mcp.server_port, rest.server_port, ["--role", "tester"],
                     extra_env={"NEBULA_MCP_BASE": f"http://127.0.0.1:{rest.server_port}"})
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert "DEGRADED" in r.stdout and "nebula-mcp" in r.stdout
        assert "FAILED" not in r.stdout
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_strict_mode_exit_one_on_degraded():
    mcp, rest = start_servers({"active_leases": []})
    try:
        r = run_shim(mcp.server_port, rest.server_port, ["--role", "tester", "--strict"],
                     extra_env={"TACKLE_MCP_BASE": "http://127.0.0.1:1"})  # nothing there
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert "DEGRADED" in r.stdout
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_forum_scan_lists_threads():
    mcp, rest = start_servers({"active_leases": []})
    try:
        r = run_shim(mcp.server_port, rest.server_port, ["--role", "tester", "--dry-run"])
        assert r.returncode == 0, r.stderr
        assert "issue: thing is broken" in r.stdout
        assert "todo: fix the thing" in r.stdout
        assert "change-log fetched" in r.stdout  # change-log read, titles not printed (lean boot output)
        assert rest_calls("/api/forums/issues-and-open-questions/threads")
        assert rest_calls("/api/forums/to-do/threads")
        assert rest_calls("/api/forums/change-log/threads")
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_procedures_loaded_via_tackle():
    mcp, rest = start_servers({"active_leases": []})
    try:
        r = run_shim(mcp.server_port, rest.server_port, ["--role", "tester", "--dry-run"])
        assert r.returncode == 0, r.stderr
        calls = mcp_calls("memory_get_procedures")
        assert calls and calls[0][1]["role"] == "tester"
        assert "2 card(s)" in r.stdout and "inbox-query-procedure" in r.stdout
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_json_report():
    mcp, rest = start_servers({"active_leases": []})
    try:
        r = run_shim(mcp.server_port, rest.server_port,
                     ["--role", "tester", "--dry-run", "--json"])
        assert r.returncode == 0, r.stderr
        lines = r.stdout.strip().splitlines()
        start = next(i for i, l in enumerate(lines) if l.strip() == "{")
        report = json.loads("\n".join(lines[start:]))
        assert report["role"] == "tester" and report["dry_run"] is True
        assert isinstance(report["steps"], list) and report["steps"]
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


def test_usage_errors_exit_2():
    mcp, rest = start_servers({})
    try:
        r = run_shim(mcp.server_port, rest.server_port, [])  # missing --role
        assert r.returncode == 2, r.returncode
        r = run_shim(mcp.server_port, rest.server_port,
                     ["--role", "tester", "--ttl", "0"])
        assert r.returncode == 2, r.returncode
    finally:
        mcp.shutdown(); mcp.server_close()
        rest.shutdown(); rest.server_close()


# ── suite entrypoint (tests/run_all.py convention) ───────────────────────
def run() -> tuple[int, int, int]:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001 — suite reports any failure
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    return passed, failed, 0


if __name__ == "__main__":
    passed, failed, skipped = run()
    print(f"\n  bin/freebuff-boot suite: {passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)
