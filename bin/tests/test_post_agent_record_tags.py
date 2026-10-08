"""Regression tests for post-agent-record.py --tags parsing.

Background (incident 2026-09-22): the JSON array form
`--tags '["to:planner","type:response"]'` was silently comma-split into
mangled text[] elements, breaking tag routing and inbox filters for 185
stored records (backup: nebula.agent_records_tags_repair_20260922).
The tool must accept both house forms and refuse to store malformed
elements.

Hermetic strategy: invoke the CLI against an unreachable nebula URL.
A correctly-parsed invocation always fails at the POST (connection
refused, rc != 0, no malformed-tags message); a malformed-tags
invocation must exit 2 with the guard message BEFORE any network call.

Background (incident 2026-09-29, DBA record 524564df): `--tags` was a plain
argparse option, so repeated `--tags` flags silently kept only the LAST
value. Every caller that day used repeated flags, so 9/9 records lost their
to:* and type:* routing and became invisible to tag-routed inbox queries.
The second half of this file covers that defect: the parse_tags() unit
contract, plus one end-to-end payload assertion (the unit tests alone
cannot catch a revert of action="append", because the helper would still be
correct while argparse handed it a one-element list).
"""

import http.server
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "post-agent-record.py"
DEAD_URL = "http://localhost:9"  # nothing listens here

MALFORMED_MSG = "refusing to store malformed tags"


def _load():
    """Import the CLI module (hyphenated filename) to unit-test parse_tags."""
    spec = importlib.util.spec_from_file_location("post_agent_record", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "parse_tags"), "parse_tags() missing from the script"
    return mod


def run_cli(tags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "-r", "dba", "-t", "parse-probe",
         "-c", "probe body", "--tags", tags, "--nebula-url", DEAD_URL],
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("tags", [
    '["to:planner","type:response"]',
    ' [ "to:planner" , "type:response" ] ',
])
def test_json_array_form_parses_and_reaches_post(tags):
    r = run_cli(tags)
    combined = r.stdout + r.stderr
    assert MALFORMED_MSG not in combined and "does not parse" not in combined
    assert "Connection refused" in combined  # died at the POST, not the guard


def test_comma_form_still_parses_and_reaches_post():
    r = run_cli("to:planner,type:response")
    combined = r.stdout + r.stderr
    assert MALFORMED_MSG not in combined
    assert "Connection refused" in combined


@pytest.mark.parametrize("tags", [
    '[to:planner,"type:response"],',          # the exact mangled input shape
    '["ok-tag", 42]',                          # non-string element
    '{"to:planner": 1}',                       # JSON object, not array
])
def test_malformed_tags_rejected_before_network(tags):
    r = run_cli(tags)
    assert r.returncode == 2, (r.stdout, r.stderr)
    combined = r.stdout + r.stderr
    assert "ERROR" in combined and "Connection refused" not in combined


# --- incident 2026-09-29: repeated --tags silently truncated -------------

def test_single_comma_string():
    assert _load().parse_tags(["to:architect,type:decision"]) == [
        "to:architect",
        "type:decision",
    ]


def test_repeated_flags_no_longer_truncate():
    # THE regression: previously only the LAST --tags value survived.
    assert _load().parse_tags(
        ["to:engineer-iii", "type:inspection", "pr-639"]
    ) == ["to:engineer-iii", "type:inspection", "pr-639"]


def test_mixed_comma_and_repeated_forms():
    assert _load().parse_tags(["to:dba,type:report", " series:nexus ", ""]) == [
        "to:dba",
        "type:report",
        "series:nexus",
    ]


def test_json_array_string_backcompat():
    assert _load().parse_tags(['["to:x","type:y"]']) == ["to:x", "type:y"]


def test_dedupe_preserves_first_seen_order():
    assert _load().parse_tags(["a,b", "b,c"]) == ["a", "b", "c"]


def test_json_fragment_rejected():
    with pytest.raises(ValueError):
        _load().parse_tags(['to:x"broken'])


def test_none_yields_empty():
    assert _load().parse_tags(None) == []


def test_repeated_flags_reach_post_with_all_tags():
    """Argparse layer: repeated --tags must ACCUMULATE into the stored payload.

    The parse_tags() unit tests above would still pass if action="append"
    were reverted, because the helper would remain correct while argparse
    kept collapsing the flags into a one-element list before calling it.
    This drives the real CLI and inspects the bytes actually POSTed.
    """
    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            captured["payload"] = json.loads(self.rfile.read(n) or b"{}")
            self.send_response(201)
            self.end_headers()
            self.wfile.write(b'{"id":"probe-capture"}')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.handle_request, daemon=True)
    th.start()
    try:
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "-r", "dba", "-t", "append-probe",
             "-c", "probe body",
             "--tags", "to:architect",
             "--tags", "type:report",
             "--tags", "series:nexus",
             "--nebula-url", f"http://127.0.0.1:{srv.server_address[1]}"],
            capture_output=True, text=True, timeout=30,
        )
    finally:
        th.join(timeout=10)
        srv.server_close()

    assert r.returncode == 0, (r.stdout, r.stderr)
    assert captured["payload"]["tags"] == [
        "to:architect", "type:report", "series:nexus",
    ]
