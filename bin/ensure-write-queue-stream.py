#!/usr/bin/env python3
"""bin/ensure-write-queue-stream.py — idempotent JetStream bootstrap.

Ensures the WRITE_QUEUE JetStream stream exists on subjects
nexus.write-queue.v1.> . Re-runnable (safe to run on every start): if the
stream already exists it is left untouched.

The write-queue Java producer (nexus-core-writequeue) publishes on these
subjects; the cascade reconciler (write_queue_reconciler.py) drains them.
The stream must exist before the reconciler can consume.

Retention: the stream is provisioned explicitly with FILE storage and
bounded retention (7-day age, 1 GiB, 1M messages). A bare
add_stream(name, subjects) relies on server defaults: storage happens to
be file, but retention is UNBOUNDED (max_age=0, max_bytes=-1,
max_msgs=-1) — an undrained write-queue grows without limit. The
write-queue is the mobile offline-buffer doctrine (intents must survive
a NATS restart), so memory-backed streams are also refused loudly.
NOTE: JetStream storage type is immutable after creation, and a config
mismatch of any kind is refused loudly (exit 1, existing state named)
rather than silently mutated — reconcile explicitly if intentional.

Config note: nats-py StreamConfig.max_age takes integer SECONDS (it
converts to nanoseconds internally; pre-converted ns overflows Go's
time.Duration on the server).

Verified 2026-09-22 against a scratch JetStream server (create,
 idempotent exact-match re-run, memory-backed refusal, config-mismatch
 refusal) and thallium's live stream (already-present path).

Usage::

    python3 bin/ensure-write-queue-stream.py [--nats nats://localhost:4222]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig

STREAM_NAME = "WRITE_QUEUE"
STREAM_SUBJECTS = ["nexus.write-queue.v1.>"]
# Retention limits: intents are idempotent (writeId) and reconciler-drained,
# so bounded retention is safe; values match the thallium provisioning.
MAX_AGE_S = 7 * 24 * 3600
MAX_BYTES = 1 << 30  # 1 GiB
MAX_MSGS = 1_000_000


def _desired_config() -> StreamConfig:
    return StreamConfig(
        name=STREAM_NAME,
        subjects=STREAM_SUBJECTS,
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        max_age=MAX_AGE_S,
        max_bytes=MAX_BYTES,
        max_msgs=MAX_MSGS,
        discard=DiscardPolicy.OLD,
        num_replicas=1,
    )


async def ensure_stream(nats_url: str) -> int:
    try:
        import nats
        from nats.js.errors import NotFoundError
    except ImportError as e:
        print(f"[ensure-write-queue-stream] FATAL: {e} — install nats-py", file=sys.stderr)
        return 1

    try:
        nc = await nats.connect(nats_url)
    except Exception as e:
        print(f"[ensure-write-queue-stream] failed to reach NATS at {nats_url}: {e}", file=sys.stderr)
        return 1

    # NOTE: JetStream's STREAM.CREATE API is create-or-UPDATE. Empirically
    # (nats-server v2.15): an update whose config differs from the existing
    # stream in ANY meaningful field (storage, max_bytes, max_age, ...) is
    # rejected server-side with 10058 'stream name already in use with a
    # different configuration' — no silent mutation happens. So the contract
    # delivered here is: create if missing; pass if config matches exactly;
    # LOUD refusal (exit 1) with a storage-accurate diagnosis otherwise.
    try:
        js = nc.jetstream()
        desired = _desired_config()
        try:
            info = await js.stream_info(STREAM_NAME)
        except NotFoundError:
            await js.add_stream(desired)
            print(
                "[ensure-write-queue-stream] WRITE_QUEUE stream created "
                f"(file storage, {MAX_AGE_S // 86400}d/{MAX_BYTES >> 30}GiB/{MAX_MSGS} msgs) on {nats_url}"
            )
            return 0

        try:
            await js.add_stream(desired)
        except Exception as e:
            if "already in use" not in str(e) and "10058" not in str(e):
                raise
            storage = str(getattr(getattr(info.config, "storage", ""), "value", info.config.storage)).lower()
            if "memory" in storage or "file" not in storage:
                print(
                    "[ensure-write-queue-stream] FATAL: WRITE_QUEUE stream exists but is "
                    f"{storage or 'non-file'}-backed; intents on it do NOT survive a NATS "
                    "restart. JetStream storage is immutable — recreate the stream "
                    "explicitly if its contents are disposable.",
                    file=sys.stderr,
                )
            else:
                print(
                    "[ensure-write-queue-stream] FATAL: WRITE_QUEUE stream exists with a "
                    "different configuration — left untouched (loud refusal, no silent "
                    f"mutation). Existing storage={storage or 'unknown'}. Compare with "
                    "the desired config in this script and reconcile explicitly.",
                    file=sys.stderr,
                )
            return 1

        print("[ensure-write-queue-stream] WRITE_QUEUE stream already present — OK (storage=file)")
        return 0
    except Exception as e:
        print(f"[ensure-write-queue-stream] FATAL: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            await nc.close()
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Ensure the WRITE_QUEUE JetStream stream exists (durable).")
    ap.add_argument("--nats", default="nats://localhost:4222", help="NATS URL")
    args = ap.parse_args()
    sys.exit(asyncio.run(ensure_stream(args.nats)))


if __name__ == "__main__":
    main()
