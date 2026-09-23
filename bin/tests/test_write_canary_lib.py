"""Hermetic tests for the write-path canary harness (moleculer/tools/write_canary_lib.py).

Pinned behaviors (ruling 59f8e2af on design 714abe42):
- normalization: server-generated UUIDs/ISO timestamps become placeholders;
  canary-marked literals survive; comparison is semantic, not byte
- deep_equal: first-difference reporting, structure-differs case
- sql_lit: safe quoting of marked values (the only interpolation path)
- Twin: JSON envelope parsing, secret header injection, raw mode
- Canary.run(): pass recording, FIRST-failure abort (containment), cleanup
  skipped after abort, residue surfaced, exit code 1 on failure/residue,
  file-based results written (window discipline)
- Case ordering preserved (cascade first is the ruling's run order)

No network, no database — Twin is exercised against a local throwaway
HTTPServer; everything else is pure.
"""
import http.server
import json
import os
import socket
import sys
import threading
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(HERE, os.pardir, os.pardir, "moleculer", "tools")
sys.path.insert(0, LIB_DIR)

import write_canary_lib as lib  # noqa: E402


# ── normalization / comparison ──────────────────────────────────────────

def test_normalize_server_uuid_and_ts():
    row = {
        "id": str(uuid.uuid4()),
        "created_at": "2026-09-23T12:34:56.789+00",
        "updated_at": "2026-09-23 12:34:56Z",
        "name": "canary-wp-keep",
    }
    n = lib.normalize(row)
    assert n["id"] == "<uuid>"
    assert n["created_at"] == "<ts>"
    assert n["updated_at"] == "<ts>"
    assert n["name"] == "canary-wp-keep"  # marked literal survives


def test_deep_equal_ignores_generated_fields():
    a = {"subject_pattern": "canary-wp-test", "id": str(uuid.uuid4())}
    b = {"subject_pattern": "canary-wp-test", "id": str(uuid.uuid4())}
    ok, diff = lib.deep_equal(a, b)
    assert ok, diff


def test_deep_equal_reports_first_difference():
    a = {"enabled": False, "x": 1}
    b = {"enabled": True, "x": 1}
    ok, diff = lib.deep_equal(a, b)
    assert not ok
    assert "enabled" in diff


def test_deep_equal_structure_mismatch():
    ok, diff = lib.deep_equal({"a": [1, 2]}, {"a": [1, 2, 3]})
    assert not ok
    assert diff


# ── sql_lit (the only interpolation path) ────────────────────────────────

def test_sql_lit_quoting():
    assert lib.sql_lit(None) == "NULL"
    assert lib.sql_lit(True) == "TRUE"
    assert lib.sql_lit(5) == "5"
    assert lib.sql_lit("canary-wp") == "'canary-wp'"
    assert lib.sql_lit("o'brien") == "'o''brien'"


def test_pg_rejects_parametrized_call():
    with pytest.raises(TypeError):
        lib.pg("SELECT $1", ["x"])


# ── Twin against a throwaway HTTP server ────────────────────────────────

class EchoHandler(http.server.BaseHTTPRequestHandler):
    seen_secret = None

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        EchoHandler.seen_secret = self.headers.get("X-Nexus-Internal")
        resp = json.dumps({"echo": json.loads(body or b"null"), "port": "stub"}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *a):  # silence
        pass


@pytest.fixture()
def stub_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), EchoHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


def test_twin_posts_json_and_injects_secret(stub_server):
    twin = lib.Twin("twin", stub_server, secret="fleet-secret")
    status, body = twin.request("POST", "/x", {"k": "v"})
    assert status == 201
    assert body["echo"] == {"k": "v"}
    assert EchoHandler.seen_secret == "fleet-secret"


def test_twin_without_secret_sends_no_header(stub_server):
    twin = lib.Twin("anon", stub_server, secret=None)
    twin.request("POST", "/x", {"k": "v"})
    assert EchoHandler.seen_secret is None


# ── Canary machinery ─────────────────────────────────────────────────────

def make_canary(tmp_path) -> lib.Canary:
    os.environ["CANARY_RESULT_DIR"] = str(tmp_path)
    c = lib.Canary("stub", 1, 2)
    return c


def test_canary_pass_series_and_result_file(tmp_path):
    c = make_canary(tmp_path)
    ran = []
    c.cases = [
        lib.Case("one", lambda c, case: ran.append("one")),
        lib.Case("two", lambda c, case: ran.append("two")),
    ]
    rc = c.run()
    assert rc == 0
    assert ran == ["one", "two"]  # ruling run-order preserved
    assert c.results[0]["ok"] and c.results[1]["ok"]
    files = os.listdir(tmp_path)
    assert any(f.startswith("write-canary-stub-") for f in files)


def test_canary_aborts_on_first_failure_and_skips_later_cases(tmp_path):
    c = make_canary(tmp_path)
    ran = []

    def boom(canary, case):
        ran.append("boom")
        raise AssertionError("mismatch: A != B")

    c.cases = [
        lib.Case("ok-first", lambda c, case: ran.append("first")),
        lib.Case("fails", boom),  # boom(case) signature -> needs (canary, case)
        lib.Case("never-runs", lambda c, case: ran.append("never")),
    ]
    rc = c.run()
    assert rc == 1
    assert c.aborted is True
    assert ran == ["first", "boom"]  # containment: no retry, no continuation


def test_canary_cleanup_skipped_after_abort_ruling_residue_preserved(tmp_path):
    """Ruling 59f8e2af: a mismatch 'leaves the residue in place, and reports'.

    So cleanup is deliberately NOT run once the series aborts — the failing
    case's partial writes must survive for inspection. Cleanup only runs for
    green cases (their own finally) and the abort stops everything after."""
    c = make_canary(tmp_path)
    cleaned = []

    def fail(case):
        raise AssertionError("x")

    c.cases = [
        lib.Case("case-a", fail, cleanup=lambda: cleaned.append("a")),
        lib.Case("case-b", lambda c, case: cleaned.append("b"),
                 cleanup=lambda: cleaned.append("cleanup-b")),
    ]
    rc = c.run()
    assert rc == 1
    assert cleaned == []  # residue preserved for inspection (no cleanup ran)
    assert c.aborted is True


def test_canary_cleanup_error_becomes_residue(tmp_path):
    c = make_canary(tmp_path)

    def bad_cleanup():
        raise RuntimeError("cleanup exploded")

    c.cases = [lib.Case("case-a", lambda c, case: None, cleanup=bad_cleanup)]
    rc = c.run()
    assert rc == 1  # residue forces failure even with green cases
    assert any("cleanup failed" in r for r in c.residue)


def test_canary_residue_alone_fails(tmp_path):
    c = make_canary(tmp_path)
    c.cases = [lib.Case("green", lambda c, case: None)]
    c.note_residue("leftover row")
    assert c.run() == 1


def test_report_includes_residue(tmp_path, capsys):
    c = make_canary(tmp_path)
    c.cases = [lib.Case("green", lambda c, case: None)]
    c.note_residue("leftover row")
    c.run()
    out = capsys.readouterr().out
    assert "RESIDUE" in out
    assert "leftover row" in out
