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
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "post-agent-record.py"
DEAD_URL = "http://localhost:9"  # nothing listens here

MALFORMED_MSG = "refusing to store malformed tags"


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
