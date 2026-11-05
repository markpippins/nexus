#!/usr/bin/env python3
"""Tests for run attribution in post-agent-record.py (V210).

Why this exists: bin/check_harness_silent_success.py had to match records to
harness runs by role and timestamp, because nothing on the record named its
run. harness-srv spawns each job with HARNESS_JOB_ID set, and children inherit
the environment, so a record posted from a shell inside the run can stamp its
own run without the model passing anything. This file pins that behaviour end
to end: the bytes actually POSTed are inspected, not just the helper.

The failure modes pinned here:
  - HARNESS_JOB_ID set  -> payload.harnessJobId equals it
  - unset              -> field absent (unattributed, not empty-string)
  - explicit flag      -> wins over the environment
  - explicit empty     -> opts out even when the environment has a value
  - unusable env value -> warning on stderr, field omitted, write still happens

That last one is the judgement call: a garbage job id is dropped rather than
refused, because losing attribution degrades an audit trail while refusing the
write loses the record.

Dual-runnable: pytest or python3 bin/tests/test_post_agent_record_harness_job_id.py.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "post-agent-record.py"
UUID = "1f0b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"


def post(extra_args: list[str], env_job_id: str | None) -> tuple[subprocess.CompletedProcess, dict]:
    """Run the CLI against a one-shot capture server; return (proc, payload)."""
    captured: dict = {}

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
    env = {"PATH": "/usr/bin:/bin"}
    if env_job_id is not None:
        env["HARNESS_JOB_ID"] = env_job_id
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "-r", "dba", "-t", "attribution-probe",
             "-c", "probe body",
             "--nebula-url", f"http://127.0.0.1:{srv.server_address[1]}",
             *extra_args],
            capture_output=True, text=True, timeout=60, env=env,
        )
    finally:
        th.join(timeout=10)
        srv.server_close()
    return proc, captured.get("payload", {})


def test_env_job_id_is_stamped() -> None:
    proc, payload = post([], UUID)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert payload.get("harnessJobId") == UUID, payload


def test_absent_env_leaves_the_record_unattributed() -> None:
    proc, payload = post([], None)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "harnessJobId" not in payload, payload


def test_explicit_flag_wins_over_the_environment() -> None:
    proc, payload = post(["--harness-job-id", "explicit-run-0001"], UUID)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert payload.get("harnessJobId") == "explicit-run-0001", payload


def test_explicit_empty_string_opts_out() -> None:
    # A caller that deliberately wants an unattributed row (e.g. a record about
    # the run itself) must be able to say so without unsetting the env.
    proc, payload = post(["--harness-job-id", ""], UUID)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "harnessJobId" not in payload, payload


def test_unusable_env_value_warns_and_still_writes() -> None:
    proc, payload = post([], "/tmp/jobs/abc12345.md")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "harnessJobId" not in payload, payload
    assert "unusable harness job id" in proc.stderr, proc.stderr
    assert "unattributed" in proc.stderr, proc.stderr


def test_whitespace_only_env_is_ignored_silently() -> None:
    # `HARNESS_JOB_ID= ` (a shell quoting slip) must not produce a warning on
    # every write: blank means unattributed, which is a normal state.
    proc, payload = post([], "   ")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "harnessJobId" not in payload, payload
    assert "unusable" not in proc.stderr, proc.stderr


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL {name}: {exc}")
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
