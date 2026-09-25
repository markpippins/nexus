#!/usr/bin/env python3
"""CI boot-smoke probe: publish ONE write-queue intent through JetStream.

Used by the D5 (phase-2) write-queue arc probe in
.github/workflows/nexus-core-image.yml. Publishes a bare WriteQueueEntry
(the reconciler's accepted shape, WriteQueueProducerTest-compatible) with an
UNMAPPED target so the reconciler takes its staging-only branch — a real
canonical apply (durable resolution.write_queue_applied row) that does not
depend on seed data, which the schema-only CI snapshot does not carry.

After publishing, asserts the intent was persisted in the WRITE_QUEUE
JetStream stream (publish to a subject no stream matches would otherwise
silently succeed as core-NATS fire-and-forget).

Usage::

    python3 bin/ci_write_queue_probe.py --nats nats://localhost:4222 \
        --write-id smoke-20260925T180000Z
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

SUBJECT_PREFIX = "nexus.write-queue.v1"
STREAM_NAME = "WRITE_QUEUE"


async def _run(nats_url: str, write_id: str) -> int:
    import nats

    nc = await nats.connect(nats_url)
    try:
        js = nc.jetstream()
        subject = f"{SUBJECT_PREFIX}.ci-probe.touch"
        entry = {
            "intent": {
                "writeId": write_id,
                "target": "ci.probe",
                "verb": "touch",
                "payload": {"probe": True},
                "actor": {"role": "ci-boot-smoke"},
                "schemaVersion": "ci-smoke",
            }
        }
        ack = await js.publish(subject, json.dumps(entry).encode())
        await nc.flush()
        print(f"published {subject} seq={ack.seq} stream={ack.stream}")
        info = await js.stream_info(STREAM_NAME)
        print(f"stream {STREAM_NAME} state: messages={info.state.messages}")
        if info.state.messages < 1:
            print(f"::error::probe intent not persisted in JetStream stream {STREAM_NAME}")
            return 1
        return 0
    finally:
        await nc.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nats", default="nats://localhost:4222")
    ap.add_argument("--write-id", required=True)
    args = ap.parse_args()
    try:
        return asyncio.run(_run(args.nats, args.write_id))
    except Exception as e:  # noqa: BLE001 — CI probe must fail loudly with context
        print(f"::error::write-queue probe failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
