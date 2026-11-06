#!/usr/bin/env python3
"""Tests for model attribution in post-agent-record.py.

Why this exists: bin/post-agent-record.py is the canonical R1/R2 record
writer, and `--model` defaulted to None with no environment fallback — even
though the neighbouring `--harness-job-id` option reads $HARNESS_JOB_ID. So a
record named the run that wrote it but not the model, unless the caller
remembered to pass the flag by hand. Measured 2026-11-06 against the running
database: 363 of the 500 most recent records (72.6%) carried a null model,
while the sibling poster scripts (bin/post-change-log.sh) filled in a
hardcoded fallback instead. Neither is attribution.

harness-srv exports NEXUS_AGENT_MODEL per attempt and children inherit the
environment, so inheriting it here is the same shape V210 used for the run id.
The bytes actually POSTed are inspected, not just the resolved helper.

Pinned here:
  - NEXUS_AGENT_MODEL set  -> payload.model equals it
  - unset                  -> payload.model is null (not the empty string)
  - explicit flag          -> wins over the environment
  - explicit empty         -> opts out even when the environment has a value
  - unusable env value     -> warning on stderr, model omitted, write happens
  - whitespace-only env    -> ignored silently

That last judgement matches the job-id suite: losing attribution degrades an
audit trail, refusing the write loses the record.

Dual-runnable: pytest or
python3 bin/tests/test_post_agent_record_model_attribution.py.
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
MODEL = "opencode/big-pickle"


def post(extra_args: list[str], env_model: str | None) -> tuple[subprocess.CompletedProcess, dict]:
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
    if env_model is not None:
        env["NEXUS_AGENT_MODEL"] = env_model
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


def test_env_model_is_stamped() -> None:
    proc, payload = post([], MODEL)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert payload.get("model") == MODEL, payload


def test_absent_env_leaves_the_record_unattributed() -> None:
    proc, payload = post([], None)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    # null, not "": an empty string would look like a model named "".
    assert payload.get("model") is None, payload


def test_explicit_flag_wins_over_the_environment() -> None:
    proc, payload = post(["--model", "freebuff/explicit-0001"], MODEL)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert payload.get("model") == "freebuff/explicit-0001", payload


def test_explicit_empty_string_opts_out() -> None:
    # A caller posting a record about the run itself must be able to say
    # "unattributed" without unsetting the inherited environment.
    proc, payload = post(["--model", ""], MODEL)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert payload.get("model") is None, payload


def test_unusable_env_value_warns_and_still_writes() -> None:
    proc, payload = post([], "/tmp/not-a-model")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert payload.get("model") is None, payload
    assert "unusable model id" in proc.stderr, proc.stderr


def test_whitespace_only_env_is_ignored_silently() -> None:
    proc, payload = post([], "   ")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert payload.get("model") is None, payload
    assert "unusable" not in proc.stderr, proc.stderr


def test_slash_and_dot_ids_are_accepted() -> None:
    # The vendor-qualified ids actually in use must survive the regex.
    for candidate in ("freebuff/gpt-6-luna", "openai/gpt-oss-120b",
                      "claude-sonnet-4-5", "opencode-go/glm-5.3-flash"):
        proc, payload = post([], candidate)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert payload.get("model") == candidate, payload


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
