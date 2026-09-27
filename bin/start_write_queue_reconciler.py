#!/usr/bin/env python3
"""bin/start_write_queue_reconciler.py — Option A deployment wrapper.

Starts python/cascade/write_queue_reconciler.py ONLY after its startup
dependencies are verified, in the gate order codified on ruling thread
f63bfbc7 (Option A: keep the reconciler fail-fast; make the deployment
responsible for its prerequisites):

  1. PROVISION     bin/ensure-write-queue-stream.py ensures the WRITE_QUEUE
                   JetStream stream exists (idempotent; refuses config
                   drift loudly). With this wrapper, stream provisioning is
                   a deployment prerequisite, NOT a reconciler concern.
  2. READINESS     NATS must accept client connections (a real
                   nats.connect() from this process — server up is not
                   enough, the client handshake is the proof).
  3. PRE-EXISTENCE resolution.write_queue_applied must exist, via
                   to_regclass — same assert-dont-create posture as the
                   reconciler (canonical DDL:
                   sql/ci-bootstrap/nexus-ci-bootstrap.sql, CREATE at
                   ~line 21765, PK added via separate ALTER at ~line
                   27148). Missing table OR missing PK = snapshot drift
                   → refuse before start (the reconciler would only find
                   out lazily on the first staged message).
  4. START         start the reconciler (fail-fast semantics preserved).
  5. ATTACH-WAIT   wait until the durable consumer is attached (the
                   reconciler's "durable consumer ... attached" log line)
                   before reporting success. A reconciler that exits early
                   (e.g. stream vanished between gates) surfaces here with
                   its exit code instead of silently "running".

Fail-fast is the point (Option A): a reconciler that cannot see its
stream must not sit in a retry loop hiding the drift — the gates above
catch it before start, and anything that slips past them still exits
loudly.

After attach this wrapper stays alive as a thin supervisor: the child
owns the process lifetime, the wrapper mirrors its exit code and
forwards ^C/SIGTERM. The child's output goes to a log FILE (never a
pipe this wrapper stops reading — a pipe would fill at ~64KB and block
the reconciler mid-run).

Config (env):
  NATS_URL                      default nats://localhost:4222
  DATABASE_URL                  default postgres://pguser:pgpass@localhost:5432/nexus
  WRITE_QUEUE_RECONCILER        path to the reconciler script
                                (default: python/cascade/write_queue_reconciler.py
                                relative to the repo root inferred from this
                                file's location)
  WRITE_QUEUE_GATE_TIMEOUT_S    readiness/attach-wait budget (default 60)
  WRITE_QUEUE_RECONCILER_LOG    child log file
                                (default /tmp/nexus-writequeue-reconciler.log)

Usage:
  python3 bin/start_write_queue_reconciler.py            # gates + supervise
  python3 bin/start_write_queue_reconciler.py --dry-run  # gates only
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_RECONCILER = REPO_ROOT / "python" / "cascade" / "write_queue_reconciler.py"
HELPER = REPO_ROOT / "bin" / "ensure-write-queue-stream.py"

STAGING_TABLE = "resolution.write_queue_applied"
# Reconciler logs this line once the durable push consumer is subscribed.
ATTACH_PATTERN = re.compile(r"durable consumer '.+' attached to")
# NOTE: connect()'s first positional parameter IS `servers` — passing the
# URL positionally AND servers= as a kwarg raises "got multiple values for
# argument 'servers'" (caught live by CI dispatch run 36197088565).
NATS_READY_SNIPPET = 'import nats; nats.connect("%(url)s", max_reconnect_attempts=1, connect_timeout=2).close()'


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def gate_provision(nats_url: str) -> tuple[bool, str]:
    """Gate 1 — ensure the WRITE_QUEUE stream exists (idempotent)."""
    if not HELPER.exists():
        return False, f"provisioning helper missing: {HELPER}"
    try:
        r = subprocess.run(
            [sys.executable, str(HELPER), "--nats", nats_url],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "ensure-write-queue-stream timed out after 60s"
    if r.returncode != 0:
        return False, f"ensure-write-queue-stream failed (exit {r.returncode}): {(r.stderr or '').strip()[:400]}"
    return True, "stream ensured"


def gate_readiness(nats_url: str, timeout_s: int) -> tuple[bool, str]:
    """Gate 2 — NATS accepts real client connections (nats.connect)."""
    probe = NATS_READY_SNIPPET % {"url": nats_url}
    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        try:
            r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            last_err = "connect attempt timed out"
            r = None
        if r is not None and r.returncode == 0:
            return True, "NATS accepting client connections"
        if r is not None and (r.stderr or "").strip():
            last_err = (r.stderr or "").strip().splitlines()[-1]
        time.sleep(2)
    return False, f"NATS never became ready within {timeout_s}s (last: {last_err})"


def gate_preexistence(dsn: str) -> tuple[bool, str]:
    """Gate 3 — resolution.write_queue_applied exists with its write_id PK."""
    try:
        import psycopg2
    except ImportError as e:
        return False, f"psycopg2 unavailable for pre-existence gate: {e}"
    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
    except Exception as e:
        return False, f"staging pre-existence gate cannot reach PG: {e}"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (STAGING_TABLE,))
            row = cur.fetchone()
            if not row or not row[0]:
                return False, (
                    f"{STAGING_TABLE} does not exist — snapshot drift; reconcile from canonical DDL "
                    f"sql/ci-bootstrap/nexus-ci-bootstrap.sql (assert-dont-create)"
                )
            cur.execute(
                "SELECT 1 FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE con.contype = 'p' AND rel.relname = 'write_queue_applied'"
            )
            if cur.fetchone() is None:
                return False, (
                    f"{STAGING_TABLE} exists WITHOUT its write_id primary key — snapshot drift "
                    f"(canonical DDL adds the PK via separate ALTER TABLE); ON CONFLICT (write_id) "
                    f"staging would fail loudly on first message"
                )
        return True, f"{STAGING_TABLE} present with PK"
    finally:
        conn.close()


def _log_has_attach(log_path: pathlib.Path) -> bool:
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return False
    return ATTACH_PATTERN.search(text) is not None


def _tail(path: pathlib.Path, n: int) -> list[str]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


def start_and_wait_attach(argv: list[str], timeout_s: int, log_path: pathlib.Path) -> int:
    """Gates 4+5 — start the reconciler and wait for durable-consumer attach.

    The child writes to a log FILE, so it can never block on a full pipe.
    This wrapper tails the log until the attach line or the child exits,
    then stays alive mirroring the child's exit code (the reconciler owns
    its process lifetime; ^C/SIGTERM on the wrapper terminates the child).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", buffering=1) as logf:
        proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT)
        print(f"[start-write-queue-reconciler] reconciler pid {proc.pid}; log: {log_path}", file=sys.stderr)

        def _stop_child(signum: int, frame: object) -> None:
            proc.terminate()

        prev_term = signal.signal(signal.SIGTERM, _stop_child)
        prev_int = signal.signal(signal.SIGINT, _stop_child)
        try:
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if _log_has_attach(log_path):
                    print(f"[start-write-queue-reconciler] durable consumer attached — wrapper stays "
                          f"alive as supervisor (pid {proc.pid}); SIGTERM/^C stops both", file=sys.stderr)
                    return proc.wait()
                rc = proc.poll()
                if rc is not None:
                    print(f"[start-write-queue-reconciler] reconciler exited (code {rc}) before "
                          f"attaching — fail-fast surfaced; last log lines:", file=sys.stderr)
                    for line in _tail(log_path, 20):
                        print("  " + line.rstrip(), file=sys.stderr)
                    return rc
                time.sleep(0.5)
            print(f"[start-write-queue-reconciler] attach not observed within {timeout_s}s — terminating "
                  f"the unverified instance (SINGLE INSTANCE doctrine: never leave a consumer "
                  f"whose attach was never proven running beside a supervisor exit); tail:", file=sys.stderr)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            for line in _tail(log_path, 10):
                print("  " + line.rstrip(), file=sys.stderr)
            return 1
        finally:
            signal.signal(signal.SIGTERM, prev_term)
            signal.signal(signal.SIGINT, prev_int)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Option A gated start for the write-queue reconciler")
    ap.add_argument("--dry-run", action="store_true", help="run gates 1-3 only; print verdicts and exit")
    args = ap.parse_args(argv)

    nats_url = _env("NATS_URL", "nats://localhost:4222")
    dsn = _env("DATABASE_URL", "postgres://pguser:pgpass@localhost:5432/nexus")
    timeout_s = int(_env("WRITE_QUEUE_GATE_TIMEOUT_S", "60"))
    reconciler = pathlib.Path(_env("WRITE_QUEUE_RECONCILER", str(DEFAULT_RECONCILER)))
    log_path = pathlib.Path(_env("WRITE_QUEUE_RECONCILER_LOG", "/tmp/nexus-writequeue-reconciler.log"))

    verdicts: list[tuple[str, bool, str]] = []
    verdicts.append(("provision", *gate_provision(nats_url)))
    verdicts.append(("readiness", *gate_readiness(nats_url, timeout_s)))
    verdicts.append(("pre-existence", *gate_preexistence(dsn)))

    failed = [v for v in verdicts if not v[1]]
    for name, ok, detail in verdicts:
        mark = "PASS" if ok else "FAIL"
        print(f"[start-write-queue-reconciler] gate {name}: {mark} — {detail}", file=sys.stderr)
    if failed:
        print(f"[start-write-queue-reconciler] REFUSING to start: {len(failed)} gate(s) failed "
              f"(fail-fast preserved; fix deployment prerequisites)", file=sys.stderr)
        return 1
    if args.dry_run:
        print("[start-write-queue-reconciler] --dry-run: all gates pass (reconciler NOT started)", file=sys.stderr)
        return 0
    if not reconciler.exists():
        print(f"[start-write-queue-reconciler] reconciler script missing: {reconciler}", file=sys.stderr)
        return 1

    return start_and_wait_attach([sys.executable, str(reconciler)], timeout_s, log_path)


if __name__ == "__main__":
    sys.exit(main())
