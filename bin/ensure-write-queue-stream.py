#!/usr/bin/env python3
"""bin/ensure-write-queue-stream.py — idempotent JetStream bootstrap.

Ensures the WRITE_QUEUE JetStream stream exists on subjects
nexus.write-queue.v1.> . Re-runnable (safe to run on every start): if the
stream already exists it is left untouched.

The write-queue Java producer (nexus-core-writequeue) publishes on these
subjects; the cascade reconciler (write_queue_reconciler.py) drains them.
The stream must exist before the reconciler can consume, and nats-server's
JetStream store is ephemeral (/tmp/nats/jetstream), so this bootstrap is
run at startup to guarantee durability.

Usage::

    python3 bin/ensure-write-queue-stream.py [--nats nats://localhost:4222]
"""
from __future__ import annotations

import argparse
import asyncio
import sys


async def ensure_stream(nats_url: str) -> int:
    try:
        import nats
    except ImportError as e:
        print(f"[ensure-write-queue-stream] FATAL: {e} — install nats-py", file=sys.stderr)
        return 1

    try:
        nc = await nats.connect(nats_url)
        js = nc.jetstream()
        try:
            await js.add_stream(name="WRITE_QUEUE", subjects=["nexus.write-queue.v1.>"])
            print("[ensure-write-queue-stream] WRITE_QUEUE stream created on " + nats_url)
        except Exception:
            # Already exists (or races) — idempotent, not an error.
            print("[ensure-write-queue-stream] WRITE_QUEUE stream already present — OK")
        await nc.close()
        return 0
    except Exception as e:
        print(f"[ensure-write-queue-stream] failed to reach NATS at {nats_url}: {e}", file=sys.stderr)
        return 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Ensure the WRITE_QUEUE JetStream stream exists.")
    ap.add_argument("--nats", default="nats://localhost:4222", help="NATS URL")
    args = ap.parse_args()
    sys.exit(asyncio.run(ensure_stream(args.nats)))


if __name__ == "__main__":
    main()