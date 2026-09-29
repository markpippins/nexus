#!/usr/bin/env python3
"""Unit tests for bin/check-inbox.sh pointer semantics.

Hermetic: spins up a mock Streamable-HTTP JSON-RPC MCP server and points
check-inbox.sh at it via the NEBULA_MCP_BASE env override, so no real
nebula-mcp / database is touched. Pins the behaviors documented in
AGENTS.md R17 and the inbox-query-procedure memory card:

- `--pointer` / `--since` are **non-destructive**: they override
  createdAfter for that call only; the stored pointer is never written.
- `--update-pointer` advances the stored pointer to the newest record's
  createdAt (converted to ISO), including when combined with --pointer
  (catch-up through the reviewed window, not a rewind to the override).
- The default path uses the single-call `nebula_get_inbox` tool; the
  explicit paths use `nebula_list_agent_records` with createdAfter.
- The default limit is 50 (raised from 10 after a request was buried below
  the old fold), and a truncated window is surfaced: WARN on stdout, and a
  REFUSAL (exit 3) to advance the pointer, since advancing would mark
  below-fold records seen without ever showing them.

Usage:
    python3 tests/bin/checks.py          # run this suite
    python3 tests/run_all.py bin         # via the repo runner

Exit code: 0 if all pass, 1 otherwise.
"""

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

NEXUS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(NEXUS_ROOT, "bin", "check-inbox.sh")

# ── fixtures ──────────────────────────────────────────────────────────────
# Records carry createdAt as epoch ms (like nebula agent records).
T1 = 1_752_000_000_000  # older
T2 = 1_752_000_360_000  # newer (+6 min)
STORE_POINTER = "2026-08-01T00:00:00Z"

REC_OLDER = {"createdAt": T1, "recordType": "engineering_log", "title": "older record"}
REC_NEWER = {"createdAt": T2, "recordType": "report", "title": "newer record"}


def iso_of(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── mock MCP server (Streamable-HTTP, stateless) ─────────────────────────
class MockMcp(BaseHTTPRequestHandler):
    """Minimal JSON-RPC server. config/calls are class-level so each test
    seeds fixtures via start_server() and inspects recorded calls."""

    config = {"stored_pointer": STORE_POINTER, "records": []}
    calls: list[tuple] = []  # (method, params) in request order

    def log_message(self, *a):  # silence request logging
        pass

    def _send_json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode()
        try:
            req = json.loads(raw)
        except Exception:
            self._send_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return
        method = req.get("method", "")
        params = req.get("params") or {}
        self.__class__.calls.append((method, params))

        if method == "initialize":
            self._send_json({"jsonrpc": "2.0", "id": req.get("id"), "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "serverInfo": {"name": "mock-mcp", "version": "1.0"},
            }})
        elif method == "notifications/initialized":
            self._send_json({"jsonrpc": "2.0", "id": None, "result": {}})
        elif method == "tools/list":
            self._send_json({"jsonrpc": "2.0", "id": req.get("id"), "result": {"tools": []}})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            result = self._handle_tool(name, args)
            self._send_json({"jsonrpc": "2.0", "id": req.get("id"), "result": {
                "content": [{"type": "text", "text": json.dumps(result)}],
            }})
        else:
            self._send_json({"jsonrpc": "2.0", "id": req.get("id"),
                             "error": {"code": -32601, "message": "method not found"}})

    @classmethod
    def _handle_tool(cls, name: str, args: dict) -> dict:
        cfg = cls.config
        limit = args.get("limit") or 20  # mirror nebula_get_inbox server default
        if name == "nebula_get_inbox":
            items = cfg["records"][:limit]
            return {"role": args.get("role"), "pointer": cfg["stored_pointer"],
                    "items": items, "count": len(items)}
        if name == "nebula_list_agent_records":
            # Mirror the real REST wrapper: items is the limited page, total
            # is the full match count (this is what makes truncation exact
            # on the explicit-pointer/--all path).
            items = cfg["records"][:limit]
            return {"items": items, "total": len(cfg["records"]),
                    "page": 1, "pageSize": limit}
        if name == "nebula_set_inbox_pointer":
            return {"ok": True}
        return {"items": [], "total": 0}


def start_server(config: dict):
    """Seed fixtures, reset the call log, and start the mock on a free port."""
    MockMcp.config = dict(config)
    MockMcp.calls = []
    srv = HTTPServer(("127.0.0.1", 0), MockMcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run_script(port: int, args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["NEBULA_MCP_BASE"] = f"http://127.0.0.1:{port}"
    return subprocess.run(["bash", SCRIPT] + args, capture_output=True, text=True, env=env, timeout=60)


def tool_calls(name: str) -> list[tuple]:
    return [c for c in MockMcp.calls if c[0] == "tools/call" and c[1].get("name") == name]


def list_args() -> dict:
    la = tool_calls("nebula_list_agent_records")
    assert la, "expected nebula_list_agent_records call"
    return la[0][1].get("arguments", {})


def set_pointer_args_list() -> list[dict]:
    return [c[1].get("arguments", {}) for c in tool_calls("nebula_set_inbox_pointer")]


# ── tests ────────────────────────────────────────────────────────────────
def test_default_uses_get_inbox_single_call():
    srv = start_server({"stored_pointer": STORE_POINTER, "records": [REC_NEWER, REC_OLDER]})
    try:
        r = run_script(srv.server_port, ["--role", "engineer"])
        assert r.returncode == 0, r.stderr
        assert tool_calls("nebula_get_inbox"), "default path must use nebula_get_inbox"
        assert tool_calls("nebula_list_agent_records") == [], "default path must NOT use list path"
        assert set_pointer_args_list() == [], "default path must not write the pointer"
        assert f"since {STORE_POINTER}" in r.stdout
        assert "newer record" in r.stdout and "older record" in r.stdout
    finally:
        srv.shutdown()
        srv.server_close()


def test_pointer_is_non_destructive():
    OLDER = "2026-07-01T00:00:00Z"
    srv = start_server({"stored_pointer": STORE_POINTER, "records": [REC_NEWER]})
    try:
        r = run_script(srv.server_port, ["--role", "engineer", "--pointer", OLDER])
        assert r.returncode == 0, r.stderr
        assert list_args().get("createdAfter") == OLDER, "createdAfter must equal the --pointer value"
        assert set_pointer_args_list() == [], "--pointer must not write the stored pointer"
        assert f"since {OLDER}" in r.stdout
        assert "newer record" in r.stdout
    finally:
        srv.shutdown()
        srv.server_close()


def test_update_pointer_advances_to_newest():
    srv = start_server({"stored_pointer": STORE_POINTER, "records": [REC_NEWER, REC_OLDER]})
    try:
        r = run_script(srv.server_port, ["--role", "engineer", "--update-pointer"])
        assert r.returncode == 0, r.stderr
        args = set_pointer_args_list()
        assert len(args) == 1, f"expected exactly one pointer write, got {len(args)}"
        assert args[0]["timestamp"] == iso_of(T2), "pointer must advance to the NEWEST record"
        assert f"# pointer updated to {iso_of(T2)}" in r.stdout
    finally:
        srv.shutdown()
        srv.server_close()


def test_pointer_with_update_catches_up_through_window():
    OLDER = "2026-07-01T00:00:00Z"
    srv = start_server({"stored_pointer": STORE_POINTER, "records": [REC_NEWER, REC_OLDER]})
    try:
        r = run_script(srv.server_port, ["--role", "engineer", "--pointer", OLDER, "--update-pointer"])
        assert r.returncode == 0, r.stderr
        assert list_args().get("createdAfter") == OLDER
        args = set_pointer_args_list()
        assert len(args) == 1
        assert args[0]["timestamp"] == iso_of(T2), "catch-up must go to newest in window, not the override"
    finally:
        srv.shutdown()
        srv.server_close()


def test_since_computes_relative_pointer_non_destructive():
    srv = start_server({"stored_pointer": STORE_POINTER, "records": [REC_NEWER]})
    try:
        before = datetime.now(timezone.utc)
        r = run_script(srv.server_port, ["--role", "engineer", "--since", "7d"])
        after = datetime.now(timezone.utc)
        assert r.returncode == 0, r.stderr
        created_after = list_args().get("createdAfter")
        assert created_after, "--since must produce a createdAfter"
        parsed = datetime.strptime(created_after, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        lo = (before - timedelta(days=7)) - timedelta(seconds=5)
        hi = (after - timedelta(days=7)) + timedelta(seconds=5)
        assert lo <= parsed <= hi, f"createdAfter {created_after} outside 7d window [{lo}, {hi}]"
        assert set_pointer_args_list() == [], "--since must not write the stored pointer"
    finally:
        srv.shutdown()
        srv.server_close()


def test_all_ignores_pointer_no_write():
    srv = start_server({"stored_pointer": STORE_POINTER, "records": [REC_NEWER]})
    try:
        r = run_script(srv.server_port, ["--role", "engineer", "--all"])
        assert r.returncode == 0, r.stderr
        assert "createdAfter" not in list_args(), "--all must not send createdAfter"
        assert set_pointer_args_list() == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_usage_errors_exit_2():
    srv = start_server({"stored_pointer": STORE_POINTER, "records": []})
    try:
        cases = [
            ["--since", "bogus"],
            ["--since", "7d", "--pointer", "2026-01-01T00:00:00Z"],
            ["--since", "7d", "--all"],
            ["--since"],
        ]
        for extra in cases:
            r = run_script(srv.server_port, ["--role", "engineer"] + extra)
            assert r.returncode == 2, (extra, r.returncode, r.stderr)
            assert r.stderr.strip().startswith("ERROR:"), (extra, r.stderr)
    finally:
        srv.shutdown()
        srv.server_close()


# ── fold / truncation semantics ───────────────────────────────────────────

def _seed_overflow(n: int) -> list[dict]:
    """n records spaced 1 min apart, oldest first (server returns as stored)."""
    base = 1_752_000_000_000
    out = []
    for i in range(n):
        out.append({"createdAt": base + i * 60_000,
                    "recordType": "report", "title": f"overflow record {i}"})
    return out


def test_default_limit_is_50():
    srv = start_server({"stored_pointer": STORE_POINTER, "records": [REC_NEWER, REC_OLDER]})
    try:
        r = run_script(srv.server_port, ["--role", "engineer"])
        assert r.returncode == 0, r.stderr
        calls = tool_calls("nebula_get_inbox")
        assert calls, "default path must use nebula_get_inbox"
        assert calls[0][1].get("arguments", {}).get("limit") == 50, \
            "default limit must be 50 (was 10 — that fold buried requests)"
        assert "WARN" not in r.stdout, "a 2-record window must not warn"
    finally:
        srv.shutdown()
        srv.server_close()


def test_truncation_warns_and_refuses_pointer_advance_default_path():
    # 55 records vs default 50: the default path only reports the returned
    # count, so the warning is the (count == limit) heuristic — and the
    # pointer advance must REFUSE rather than mark the bottom 5 seen unseen.
    newest_iso = iso_of(1_752_000_000_000 + 54 * 60_000)
    srv = start_server({"stored_pointer": STORE_POINTER, "records": _seed_overflow(55)})
    try:
        r = run_script(srv.server_port, ["--role", "engineer", "--update-pointer"])
        assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
        assert "WARN" in r.stdout and "below the fold" in r.stdout
        assert set_pointer_args_list() == [], "truncated window must not advance the pointer"
        assert "REFUSING" in r.stderr
        assert newest_iso not in r.stdout, "newest record was below the fold and must not print"
    finally:
        srv.shutdown()
        srv.server_close()


def test_truncation_is_exact_on_list_path_and_refuses_advance():
    # --pointer path uses nebula_list_agent_records, whose wrapper carries a
    # full `total`: the warning can state exact numbers instead of a heuristic.
    srv = start_server({"stored_pointer": STORE_POINTER, "records": _seed_overflow(55)})
    try:
        r = run_script(srv.server_port, ["--role", "engineer", "--pointer", STORE_POINTER,
                                         "--limit", "50", "--update-pointer"])
        assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
        assert "showing 50 of 55" in r.stdout, r.stdout
        assert set_pointer_args_list() == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_higher_limit_recovers_and_advances():
    # The recovery path the warning prescribes must actually work.
    newest_iso = iso_of(1_752_000_000_000 + 54 * 60_000)
    srv = start_server({"stored_pointer": STORE_POINTER, "records": _seed_overflow(55)})
    try:
        r = run_script(srv.server_port, ["--role", "engineer", "--limit", "60", "--update-pointer"])
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert "WARN" not in r.stdout
        args = set_pointer_args_list()
        assert len(args) == 1 and args[0]["timestamp"] == newest_iso, \
            "untruncated window must advance to the true newest"
    finally:
        srv.shutdown()
        srv.server_close()


# ── inbox role case normalization (measured live 2026-09-29) ─────────────
# The routing tag `to:<role>` is matched EXACTLY by nebula-srv (`= ANY(tags)`),
# so a case variant does not error -- it silently addresses a different mailbox.
# Live at the time of writing: 694 records tagged `to:dba` against 7 tagged
# `to:DBA`, and two live Redis pointers six days apart. The canonical
# vocabulary is already lowercase, so the query is normalized rather than the
# stored data rewritten.


def test_inbox_tag_is_lowercased_for_mixed_case_role():
    srv = start_server({"stored_pointer": None, "records": [REC_NEWER]})
    try:
        r = run_script(srv.server_port, ["--role", "DBA", "--all"])
        assert r.returncode == 0, r.stderr
        assert list_args()["tag"] == ["to:dba"], (
            f"mixed-case role must query the canonical lowercase tag, got "
            f"{list_args()['tag']!r}"
        )
    finally:
        srv.shutdown()
        srv.server_close()


def test_inbox_tag_is_lowercased_for_mixed_case_role_with_pointer():
    srv = start_server({"stored_pointer": None, "records": [REC_NEWER]})
    try:
        r = run_script(srv.server_port, ["--role", "DBA", "--pointer", "2026-07-01T00:00:00Z"])
        assert r.returncode == 0, r.stderr
        assert list_args()["tag"] == ["to:dba"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_inbox_tag_still_omits_author_role_filter():
    # Guard the fix against reintroducing an over-correction: addressing is by
    # TAG only. Passing author `role` as well would intersect to zero.
    srv = start_server({"stored_pointer": None, "records": [REC_NEWER]})
    try:
        r = run_script(srv.server_port, ["--role", "DBA", "--all"])
        assert r.returncode == 0, r.stderr
        assert "role" not in list_args(), "must not filter by author role"
    finally:
        srv.shutdown()
        srv.server_close()


def test_lowercase_role_is_unchanged_by_normalization():
    srv = start_server({"stored_pointer": None, "records": [REC_NEWER]})
    try:
        r = run_script(srv.server_port, ["--role", "dba", "--all"])
        assert r.returncode == 0, r.stderr
        assert list_args()["tag"] == ["to:dba"]
    finally:
        srv.shutdown()
        srv.server_close()


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
    print(f"\n  bin suite: {passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)
