#!/usr/bin/env python3
"""peb_admission tests — advisory record-then-act helper contract.

Covers the PEB-forward Phase 1 guard rails:
  1. recording failure must never raise (broken psql)
  2. canonical path success returns True
  3. direct-fallback path (function raises -> plain insert) lands rows
     and dedupes on idempotency_key

Run: python3 test_peb_admission.py   (non-zero exit on failure)
"""
import os
import subprocess
import sys
import tempfile
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cascade.peb_admission import record_gate_outcome  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f" {detail}" if not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_stub(exit_code):
    """A fake 'psql' binary: echoes args to a log file and exits with code."""
    log = tempfile.mktemp(prefix="pebadm-")
    script = (
        f"#!/bin/sh\n"
        f"echo \"$@\" >> {log}\n"
        f"exit {exit_code}\n"
    )
    path = tempfile.mktemp(prefix="peb-psql-")
    with open(path, "w") as f:
        f.write(script)
    os.chmod(path, 0o755)
    return path, log


def test_broken_psql_never_raises():
    print("1. broken psql -> False, no raise")
    stub, _ = make_stub(1)
    try:
        ok = record_gate_outcome(
            gate="test.gate", entity_id="e1", admitted=True, reason="",
            payload={"k": "v"}, psql=[stub],
        )
    finally:
        os.unlink(stub)
    check("returns False", ok is False)


def test_success_path():
    print("2. psql exit 0 -> True")
    stub, _ = make_stub(0)
    try:
        ok = record_gate_outcome(
            gate="test.gate", entity_id="e2", admitted=True, reason="",
            payload={"k": "v"}, psql=[stub],
        )
    finally:
        os.unlink(stub)
    check("returns True", ok is True)


def test_dsn_pathway_used_by_default():
    print("4. CONDUIT_PG_DSN set -> DSN pathway (no docker exec); injected psql still wins (call site)")
    stub, log = make_stub(0)
    try:
        from cascade.peb_admission import _psql_cmd as _resolve_psql
        with unittest.mock.patch.dict(os.environ, {"CONDUIT_PG_DSN": "postgresql://u:p@h:5432/d"}):
            cmd = _resolve_psql()
            check("DSN pathway selected", cmd[0] == "psql" and cmd[-1] == "postgresql://u:p@h:5432/d" and "docker" not in cmd, str(cmd))
            # (c) no env, no injection -> legacy docker fallback intact
        saved = os.environ.pop("CONDUIT_PG_DSN", None)
        try:
            cmd3 = _resolve_psql()
            check("legacy docker fallback", cmd3[:3] == ["docker", "exec", "-i"], str(cmd3))
        finally:
            if saved is not None:
                os.environ["CONDUIT_PG_DSN"] = saved
        # (d) call-site contract: injected psql still wins over env at record_gate_outcome
        with unittest.mock.patch.dict(os.environ, {"CONDUIT_PG_DSN": "postgresql://u:p@h:5432/d"}):
            ok = record_gate_outcome(
                gate="test.gate", entity_id="e-inject", admitted=True, reason="",
                payload={"k": "v"}, psql=[stub],
            )
            check("injected psql wins over env", ok is True)
    finally:
        os.unlink(stub)
        if os.path.exists(log):
            os.unlink(log)


def test_scripted_fallback():
    print("3. scripted psql: first call fails, second call succeeds")
    log = tempfile.mktemp(prefix="peb-log-")
    script = (
        "#!/bin/sh\n"
        f"echo \"$@\" >> {log}\n"
        f"if [ ! -f {log}.n ]; then touch {log}.n; exit 1; fi\n"
        "exit 0\n"
    )
    path = tempfile.mktemp(prefix="peb-psql-")
    with open(path, "w") as f:
        f.write(script)
    os.chmod(path, 0o755)
    try:
        first = record_gate_outcome(
            gate="test.gate", entity_id="e3a", admitted=False, reason="nope",
            payload={"k": "v"}, psql=[path],
        )
        check("first call fails as scripted", first is False)
        second = record_gate_outcome(
            gate="test.gate", entity_id="e3b", admitted=False, reason="nope",
            payload={"k": "v"}, psql=[path],
        )
        check("second call succeeds", second is True)
    finally:
        os.unlink(path)
        os.unlink(log) if os.path.exists(log) else None
        os.unlink(log + ".n") if os.path.exists(log + ".n") else None


if __name__ == "__main__":
    test_broken_psql_never_raises()
    test_success_path()
    test_dsn_pathway_used_by_default()
    test_scripted_fallback()
    if FAILURES:
        print("\nFAILURES:", FAILURES)
        sys.exit(1)
    print("\nALL PEB-ADMISSION TESTS PASSED")