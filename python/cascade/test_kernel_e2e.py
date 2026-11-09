#!/usr/bin/env python3
"""test_kernel_e2e.py — Full kernel pipeline integration test.

Verifies the complete event flow:

    sys_transition() → BEFORE INSERT trigger (policy) → AFTER INSERT trigger (NOTIFY)
        → kernel_subscriber (LISTEN → NATS)
        → projection_updater (NATS → kernel.event_log)

Steps:
  1. Verify PostgreSQL (kernel schema) and NATS are reachable
  2. Start kernel_subscriber.py (LISTEN → NATS) as a subprocess
  3. Start projection_updater.py (NATS → kernel.event_log) as a subprocess
  4. Call SELECT kernel.sys_transition() with a test event
  5. Poll kernel.event_log until the event appears (30s timeout)
  6. Verify the projected row has correct fields
  7. Clean up subprocesses

Usage::

    cd /home/codex/dev/nexus/python/cascade
    python3 test_kernel_e2e.py
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid

import pathlib

_GUARD_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "docker", doing="these tests run PostgreSQL inside a Docker container"
)

# ── Configuration ───────────────────────────────────────────────────
CASCADE_DIR = os.path.dirname(os.path.abspath(__file__))
PSQL_CMD = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus"]
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgres://pguser:pgpass@localhost:5432/nexus",
)

# We'll capture the auto-generated event_id after calling sys_transition()
TEST_RUN_ID = uuid.uuid4().hex[:12]


# ── Helpers ─────────────────────────────────────────────────────────


def psql(sql: str, timeout: int = 15) -> tuple[int, str]:
    """Run a SQL query and return (returncode, stdout)."""
    result = subprocess.run(
        PSQL_CMD + ["-t", "-A"],
        input=sql,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout.strip()


def psql_json(sql: str, timeout: int = 15) -> dict | None:
    """Run a SQL query and parse the first result row as JSON."""
    rc, out = psql(sql, timeout=timeout)
    if rc == 0 and out and out != "(0 rows)":
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None
    return None


def check_dependencies() -> bool:
    """Verify PostgreSQL and NATS are reachable."""
    print("[test] Checking dependencies...")

    # PostgreSQL reachable + kernel schema exists
    rc, out = psql("SELECT 1;")
    if rc != 0 or out != "1":
        print(f"  ❌ PostgreSQL unreachable: rc={rc} out={out}")
        return False
    print("  ✅ PostgreSQL reachable")

    # Verify kernel schema
    rc, out = psql(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'kernel' AND table_name = 'transition_event';"
    )
    if rc != 0 or out.strip() != "1":
        print(f"  ❌ kernel.transition_event not found")
        return False
    print("  ✅ kernel schema present")

    # NATS reachable
    try:
        host, port_str = NATS_URL.replace("nats://", "").split(":")
        port = int(port_str.split("/")[0])
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        print("  ✅ NATS reachable")
    except Exception as e:
        print(f"  ❌ NATS unreachable: {e}")
        return False

    return True


def start_subscriber() -> subprocess.Popen | None:
    """Start kernel_subscriber.py as a background process."""
    try:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(CASCADE_DIR, "kernel_subscriber.py")],
            env={**os.environ, "DATABASE_URL": DATABASE_URL, "NATS_URL": NATS_URL},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        print(f"  ✅ kernel_subscriber started (PID {proc.pid})")
        return proc
    except Exception as e:
        print(f"  ❌ Failed to start kernel_subscriber: {e}")
        return None


def start_updater() -> subprocess.Popen | None:
    """Start projection_updater.py as a background process."""
    try:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(CASCADE_DIR, "projection_updater.py")],
            env={**os.environ, "DATABASE_URL": DATABASE_URL, "NATS_URL": NATS_URL},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        print(f"  ✅ projection_updater started (PID {proc.pid})")
        return proc
    except Exception as e:
        print(f"  ❌ Failed to start projection_updater: {e}")
        return None


def emit_transition_event() -> str | None:
    """Call kernel.sys_transition() and return the auto-generated event_id.

    Uses a valid event_type from the kernel.event_type enum.
    The event is inserted via the CQRS-style sys_transition() function,
    which fires BEFORE INSERT (authorization) and AFTER INSERT (NOTIFY) triggers.
    """
    sql = f"""
    SELECT kernel.sys_transition(
        'transition.requested'::kernel.event_type,
        'e2e-test',
        'runner-{TEST_RUN_ID}',
        'test-runner',
        '{{"test_run": "{TEST_RUN_ID}", "source": "test_kernel_e2e.py"}}'::jsonb,
        'test-runner'
    );
    """
    rc, out = psql(sql)
    if rc != 0 or not out:
        print(f"  ❌ sys_transition() failed: rc={rc} out={repr(out[:200])}")
        return None
    # Extract event_id from the returned transition_event row
    # sys_transition returns a kernel.transition_event row as text
    event_id = out.strip()
    if event_id.startswith("("):
        # psql tuple output: (col1,col2,...)
        # event_id is the 2nd column (id is 1st, event_id is 2nd)
        parts = event_id.strip("()").split(",")
        if len(parts) >= 2:
            event_id = parts[1].strip()
    print(f"  ✅ Event inserted: {event_id}")
    return event_id


def poll_event_log(event_id: str, timeout: int = 30) -> dict | None:
    """Poll kernel.event_log until the specified event appears."""
    deadline = time.time() + timeout
    poll_interval = 0.5
    while time.time() < deadline:
        row = psql_json(f"""
        SELECT row_to_json(el.*)::text
        FROM kernel.event_log el
        WHERE el.event_id = '{event_id}'::uuid;
        """)
        if row:
            return row
        time.sleep(poll_interval)
    return None


# ── Main ────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("Kernel E2E Pipeline Test")
    print(f"  Run ID: {TEST_RUN_ID}")
    print("=" * 60)

    # Step 1: Check dependencies
    if not check_dependencies():
        return 1

    # Step 2: Start subscriber and updater
    sub_proc = start_subscriber()
    if not sub_proc:
        return 1

    up_proc = start_updater()
    if not up_proc:
        sub_proc.terminate()
        return 1

    # Give them a moment to connect
    time.sleep(2)

    # Check they're still alive
    for name, proc in [("kernel_subscriber", sub_proc), ("projection_updater", up_proc)]:
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            print(f"  ❌ {name} died early:\n{stderr[:500]}")
            sub_proc.terminate()
            up_proc.terminate()
            return 1
    print("  ✅ Both services running")

    # Step 3: Emit a transition event
    print("\n[step] Emitting test event...")
    event_id = emit_transition_event()
    if not event_id:
        sub_proc.terminate()
        up_proc.terminate()
        return 1

    # Step 4: Poll kernel.event_log for the projection
    print(f"\n[step] Polling kernel.event_log for up to 30s...")
    event = poll_event_log(event_id, timeout=30)

    if event is None:
        print("  ❌ Event did NOT appear in kernel.event_log within 30s")
        # Diagnostic dump
        for tbl in ["transition_event", "event_log"]:
            rc, out = psql(
                f"SELECT event_id::text, event_type, actor, authority "
                f"FROM kernel.{tbl} ORDER BY id DESC LIMIT 3;"
            )
            print(f"  kernel.{tbl}:\n{out[:400]}")
        sub_proc.terminate()
        up_proc.terminate()
        return 1

    print(f"  ✅ Event found in kernel.event_log!")
    print(f"     event_id:       {event.get('event_id')}")
    print(f"     event_type:     {event.get('event_type')}")
    print(f"     actor:          {event.get('actor')}")
    print(f"     authority:      {event.get('authority')}")
    print(f"     aggregate_type: {event.get('aggregate_type')}")
    print(f"     aggregate_id:   {event.get('aggregate_id')}")
    print(f"     reducer_version: {event.get('reducer_version')}")

    # Step 5: Verify field correctness
    errors = []
    if str(event.get("event_id", "")).strip() != event_id.strip():
        errors.append(f"event_id mismatch: {event.get('event_id')} != {event_id}")
    if event.get("actor") != "test-runner":
        errors.append(f"actor mismatch: {event.get('actor')}")
    if event.get("aggregate_type") != "e2e-test":
        errors.append(f"aggregate_type mismatch: {event.get('aggregate_type')}")
    if event.get("reducer_version") != "kernel.event_log@0.1":
        errors.append(f"reducer_version mismatch: {event.get('reducer_version')}")

    if errors:
        print("  ❌ Validation errors:")
        for e in errors:
            print(f"     - {e}")
        sub_proc.terminate()
        up_proc.terminate()
        return 1

    print("  ✅ All fields match expected values")

    # Step 6: Measure propagation latency
    if "received_at" in event and "event_timestamp" in event:
        import datetime
        received = datetime.datetime.fromisoformat(event["received_at"].replace("Z", "+00:00"))
        event_ts = datetime.datetime.fromisoformat(event["event_timestamp"].replace("Z", "+00:00"))
        latency_ms = (received - event_ts).total_seconds() * 1000
        print(f"     propagation latency: {latency_ms:.0f}ms")

    # Step 7: Clean up
    print("\n[step] Cleaning up...")
    sub_proc.terminate()
    up_proc.terminate()

    for p in [sub_proc, up_proc]:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()

    for name, proc in [("kernel_subscriber", sub_proc), ("projection_updater", up_proc)]:
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        warnings = [l for l in stderr.split("\n") if l.strip() and "WARNING" in l]
        if warnings:
            for w in warnings[:3]:
                print(f"  ⚠️  {name}: {w.strip()}")
        print(f"  ✅ {name} stopped")

    print("\n" + "=" * 60)
    print("KERNEL E2E PIPELINE TEST PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
