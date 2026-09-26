#!/usr/bin/env python3
"""bin/drain_write_queue_buffer.py — drain the producer's buffered_local JSONL fallback.

Closes the repo-wide gap noted on ruling thread f63bfbc7 (Option C rider,
implemented independently of the A/B ruling): WriteQueueProducer.bufferLocally
(jvm/spring/nexus-core/nexus-core-writequeue) appends CanonicalEnvelope JSON
lines to  $NEXUS_CORE_WRITEQUEUE_DIR/write-queue-buffer.jsonl  (default
/tmp/nexus-writequeue/write-queue-buffer.jsonl) whenever NATS/JetStream is
unreachable — and NOTHING in the repo drains it. Those intents were durable
on local disk only and never reconciled.

What this tool does (one pass):

  1. GATE      ensure the WRITE_QUEUE JetStream stream exists first, via
               bin/ensure-write-queue-stream.py (a drainer that publishes
               into a missing stream would re-create the original loss).
  2. READ      read the buffer and consume only COMPLETE lines (bytes
               [0, offset-after-last-newline]). A trailing partial line —
               an in-flight or crash-torn producer write — is never
               guessed at; it stays for a later pass.
  3. PUBLISH   republish each complete line's raw bytes (minus the JSONL
               newline — byte-fidelity with what the producer puts on the
               wire) to the envelope's embedded `subject`, validated to
               start with `nexus.write-queue.v1.` — a corrupt/wrong-stream
               subject is QUARANTINED, never republished cross-stream.
  4. QUARANTINE malformed JSON, missing subject, or out-of-stream subject
               lines are appended to <buffer>.quarantine (raw line kept
               for forensics/replay), never dropped, never force-published.
  5. ROTATE    only after EVERY complete line is published or quarantined:
               atomically rename the buffer to <buffer>.draining (the
               producer — which opens the path fresh with CREATE+APPEND on
               every append — immediately sees an empty buffer again), then
               merge the unprocessed remainder (torn partial + any appends
               that landed on the renamed inode) back onto the live buffer
               with O_APPEND. A re-check loop captures appends that landed
               between snapshot and rename (an fd opened before the rename
               still points at the renamed inode and keeps appending there).

Delivery semantics: AT-LEAST-ONCE. A crash between publish and rotate
re-publishes the same lines next run (the stale <buffer>.draining file is
detected and merged back first) — safe because the reconciler dedups on
write_id (staging idempotency via the write_id PK). A publish failure
ABORTS before any file operation: nothing is lost, the buffer keeps
everything for retry (exit 2). Only `queued`-grade JetStream acceptance
counts as drained.

Honest limitation: POSIX has no atomic "drop file prefix". The rotate model
removes the consumed prefix atomically and keeps appends interleaving-safe,
but a producer append landing in the microseconds between the final
size-check and the work-file unlink would be lost. The re-check loop
narrows this to effectively never (producers only append during outages);
it is documented rather than hidden.

Concurrency: an flock on <buffer>.drain.lock prevents two drainers at once
(exit 3 if busy). The producer needs no coordination.

Operating model: run manually after restoring NATS, or wire a systemd
timer (decision deliberately left explicit — no unit is auto-created).
Exits: 0 drained/nothing-to-do · 1 gate failure · 2 publish failure
(aborted, untruncated) · 3 another drainer holds the lock · 4 bad usage.

Usage::

    python3 bin/drain_write_queue_buffer.py [--dry-run] [--nats nats://localhost:4222]
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "bin" / "ensure-write-queue-stream.py"
STREAM_SUBJECT_PREFIX = "nexus.write-queue.v1."
BUFFER_FILENAME = "write-queue-buffer.jsonl"


def _buffer_path() -> pathlib.Path:
    """Mirror WriteQueueProducer.bufferLocally's dir/filename logic exactly."""
    d = os.environ.get("NEXUS_CORE_WRITEQUEUE_DIR", "/tmp/nexus-writequeue")
    return pathlib.Path(d) / BUFFER_FILENAME


def gate_stream(nats_url: str) -> tuple[bool, str]:
    """Gate 1 — the WRITE_QUEUE stream must exist before we republish."""
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


def _classify(line: bytes) -> tuple[str, str | None]:
    """→ (class, subject). class ∈ publish | quarantine."""
    try:
        envelope = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "quarantine", None
    if not isinstance(envelope, dict):
        return "quarantine", None
    subject = envelope.get("subject")
    if not isinstance(subject, str) or not subject.startswith(STREAM_SUBJECT_PREFIX):
        # Missing subject OR a subject on a different stream: republishing
        # either would be a guess. Quarantine keeps the raw line.
        return "quarantine", None
    return "publish", subject


def _append_bytes(path: pathlib.Path, payload: bytes) -> None:
    """Single O_APPEND write — interleaves safely with producer appends."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _recover_stale_work(work: pathlib.Path, buffer: pathlib.Path) -> bool:
    """A previous run crashed between rotate and merge/unlink: its lines were
    likely already published (at-least-once — reconciler dedups), but the
    unprocessed remainder must not be orphaned. Merge everything back onto
    the live buffer and remove the stale file. Returns True if one existed."""
    if not work.exists():
        return False
    data = work.read_bytes()
    if data:
        _append_bytes(buffer, data)
    work.unlink()
    return True


def _merge_back_remainder(work: pathlib.Path, buffer: pathlib.Path,
                          remainder_start: int) -> None:
    """Copy work bytes [remainder_start:] onto the live buffer, looping until
    the work file stops growing (captures producer appends that landed on the
    renamed inode after our snapshot). Unlinks the work file only once stable."""
    offset = remainder_start
    for _ in range(5):
        try:
            size = work.stat().st_size
        except FileNotFoundError:
            return
        if offset >= size:
            break
        with work.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
        _append_bytes(buffer, chunk)
        offset += len(chunk)
    else:
        print(f"[drain-write-queue-buffer] WARNING: work file {work} kept growing — "
              f"left in place (unmerged tail beyond byte {offset}); rerun to finish",
              file=sys.stderr)
        return
    try:
        # Final stability check immediately before unlink: an append in this
        # window would be lost, so verify size one last time.
        if work.stat().st_size == offset:
            work.unlink()
        else:
            print(f"[drain-write-queue-buffer] WARNING: appends landed on {work} "
                  f"during merge — left in place; rerun to finish", file=sys.stderr)
    except FileNotFoundError:
        pass


def drain(
    buffer_path: pathlib.Path,
    quarantine_path: pathlib.Path,
    publish_fn,
    dry_run: bool = False,
) -> tuple[int, int, int, bool]:
    """One drain pass. publish_fn(subject, payload_bytes) must raise on failure.

    Returns (published, quarantined, left_torn_tail, consumed).
    consumed=True means the complete-line prefix was removed from the live
    buffer (published lines gone; torn tail / later appends preserved).
    A publish failure raises BEFORE any file operation — the buffer stays
    byte-identical (retry safe).
    """
    work = buffer_path.with_suffix(buffer_path.suffix + ".draining")
    # Crash recovery FIRST: a stale work file from a previous run (crashed
    # between rotate and merge/unlink) is merged back onto the live buffer
    # so this same pass drains everything — no second run needed. Also
    # covers the buffer-missing-but-work-present case (the stale lines are
    # then the only copy; O_APPEND recreates the live buffer).
    _recover_stale_work(work, buffer_path)

    if not buffer_path.exists():
        return 0, 0, 0, False

    data = buffer_path.read_bytes()
    if not data:
        return 0, 0, 0, False

    consumed_offset = data.rfind(b"\n") + 1  # end of last COMPLETE line
    if consumed_offset == 0:
        # No complete line at all: single torn write in progress.
        return 0, 0, 1, False
    left_torn = 1 if consumed_offset < len(data) else 0

    published = 0
    quarantined = 0
    quarantine_f = None
    try:
        for raw in data[:consumed_offset].split(b"\n"):
            if not raw.strip():
                continue  # blank separator line — consume silently
            cls, subject = _classify(raw)
            if cls == "quarantine":
                quarantined += 1
                if not dry_run:
                    if quarantine_f is None:
                        quarantine_f = quarantine_path.open("ab")
                    quarantine_f.write(raw + b"\n")
                continue
            if not dry_run:
                publish_fn(subject, raw)  # raise on failure → abort pre-rotate
            published += 1

        if dry_run:
            return published, quarantined, left_torn, False

        if quarantine_f is not None:
            quarantine_f.flush()
            os.fsync(quarantine_f.fileno())

        # ── Rotate: atomically remove the consumed prefix. The producer
        # opens the path fresh (CREATE+APPEND) on every append, so the
        # rename makes an empty live buffer appear to it instantly.
        os.rename(buffer_path, work)
        _merge_back_remainder(work, buffer_path, consumed_offset)
        # Keep the live buffer in existence (empty) when nothing was left
        # behind — predictable for humans/timers; the producer recreates it
        # either way, but an always-present path is simpler to monitor.
        _append_bytes(buffer_path, b"")
        return published, quarantined, left_torn, True
    finally:
        if quarantine_f is not None:
            quarantine_f.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drain WriteQueueProducer's buffered_local JSONL fallback back onto the WRITE_QUEUE stream")
    ap.add_argument("--dry-run", action="store_true",
                    help="count and classify only — no publish, no quarantine, no rotate")
    ap.add_argument("--nats", default=os.environ.get("NATS_URL", "nats://localhost:4222"))
    args = ap.parse_args(argv)

    buffer_path = _buffer_path()
    quarantine_path = buffer_path.with_suffix(buffer_path.suffix + ".quarantine")
    lock_path = buffer_path.with_suffix(buffer_path.suffix + ".drain.lock")

    ok, detail = gate_stream(args.nats)
    print(f"[drain-write-queue-buffer] gate stream: {'PASS' if ok else 'FAIL'} — {detail}", file=sys.stderr)
    if not ok:
        return 1

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_f = lock_path.open("w")
    try:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("[drain-write-queue-buffer] another drainer holds the lock — exit 3", file=sys.stderr)
            return 3

        if not buffer_path.exists():
            print(f"[drain-write-queue-buffer] no buffer at {buffer_path} — nothing to drain",
                  file=sys.stderr)
            return 0

        def publish(subject: str, payload: bytes) -> None:
            import asyncio
            import nats

            async def _pub() -> None:
                # One connection per line is deliberate: this runs rarely,
                # after outages, against possibly-flaky NATS — a fresh
                # connection per publish means no stale-connection surprises,
                # and any failure aborts pre-rotate (retry-safe).
                nc = await nats.connect(args.nats, max_reconnect_attempts=1, connect_timeout=5)
                try:
                    js = nc.jetstream()
                    await js.publish(subject, payload)
                finally:
                    await nc.close()

            asyncio.run(_pub())

        try:
            published, quarantined, left_torn, consumed = drain(
                buffer_path, quarantine_path, publish, dry_run=args.dry_run)
        except Exception as e:
            print(f"[drain-write-queue-buffer] PUBLISH FAILURE — aborted BEFORE any file "
                  f"operation; buffer intact, retry safe (reconciler dedups on write_id): {e}",
                  file=sys.stderr)
            return 2

        print(f"[drain-write-queue-buffer] {'DRY-RUN' if args.dry_run else 'drained'} "
              f"{buffer_path}: published={published} quarantined={quarantined} "
              f"torn_tail_left={left_torn} consumed={consumed}", file=sys.stderr)
        return 0
    finally:
        lock_f.close()


if __name__ == "__main__":
    sys.exit(main())
