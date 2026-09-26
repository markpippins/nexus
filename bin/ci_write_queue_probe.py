#!/usr/bin/env python3
"""CI boot-smoke probe: publish write-queue intents through the D5 arc.

Used by the D5 (phase-2) write-queue arc probe in
.github/workflows/nexus-core-image.yml. Two modes:

staging (default)
    Publishes ONE bare WriteQueueEntry with an UNMAPPED target
    (ci.probe/touch) so the reconciler takes its staging-only branch — a
    real canonical apply (durable resolution.write_queue_applied row) that
    does not depend on seed data. Asserts JetStream actually persisted the
    publish (a publish to a subject no stream matches silently "succeeds"
    as core-NATS fire-and-forget).

governed
    Publishes a transition-entity intent THROUGH THE APP'S PRODUCER ROUTE
    (POST /api/solscript/transition-entity -> WriteQueueProducer.enqueue ->
    JetStream), exercising the full mobile producer path: lazy NatsConfig
    dial, subject construction, CanonicalEnvelope wrapping, graceful
    degradation. The reconciler's interpreter path decides the outcome:
      - committed  : seed data present and guards passed
      - refused    : seed data present, guards failed
      - rejected   : no seed data (empty interpreter rejects entity-not-found)
    All three are REAL governed decisions — the durable KeychainEvent lands
    in resolution.keychain_event_outbox either way. This mode fails only if
    the producer/queue/interpreter machinery breaks, so it runs green on
    both the schema-only snapshot and a future seed-carrying snapshot.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request

SUBJECT_PREFIX = "nexus.write-queue.v1"
STREAM_NAME = "WRITE_QUEUE"


async def _publish_and_assert(nats_url: str, subject: str, entry: dict) -> int:
    import nats

    nc = await nats.connect(nats_url)
    try:
        js = nc.jetstream()
        ack = await js.publish(subject, json.dumps(entry).encode())
        await nc.flush()
        print(f"published {subject} seq={ack.seq} stream={ack.stream}")
        info = await js.stream_info(STREAM_NAME)
        print(f"stream {STREAM_NAME} state: messages={info.state.messages}")
        if info.state.messages < 1:
            print(f"::error::intent not persisted in JetStream stream {STREAM_NAME}")
            return 1
        return 0
    finally:
        await nc.close()


async def _staging(nats_url: str, write_id: str) -> int:
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
    return await _publish_and_assert(nats_url, subject, entry)


async def _governed(app_url: str, write_id: str) -> int:
    """Drive the transition-entity intent through the app's producer route.

    The app publishes on our behalf (WriteQueueProducer), so there is no
    runner-side JetStream ack to assert — the route response's `outcome`
    IS the proof. ONLY 'queued' means the JetStream stream accepted and
    now durably holds the envelope. 'dropped_core_nats' means the
    producer's core-NATS fallback fired: the publish succeeds at the
    server but no stream/consumer holds it — the reconciler will never
    see the intent (vocabulary per f63bfbc7 / Option A), so it is a
    FAILURE here. 'buffered_local' / errors also fail.
    """
    body = json.dumps({
        "writeId": write_id,
        "entityId": "ci-probe-entity",
        "transitionId": "ci-probe-transition",
    }).encode()
    req = urllib.request.Request(
        f"{app_url}/api/solscript/transition-entity",
        data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.status
        out = json.loads(resp.read().decode())
    print(f"app route POST /api/solscript/transition-entity -> HTTP {status} {out}")
    if status != 202:
        print(f"::error::producer route did not accept the intent (HTTP {status})")
        return 1
    outcome = str(out.get("outcome", ""))
    if outcome != "queued":
        print(f"::error::producer outcome '{outcome}' — intent is NOT durably held on the stream "
              f"(only 'queued' proves JetStream acceptance)")
        return 1
    print(f"producer outcome: {outcome} (WriteQueueProducer -> JetStream accepted)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nats", default="nats://localhost:4222")
    ap.add_argument("--write-id", required=True)
    ap.add_argument("--mode", choices=("staging", "governed"), default="staging")
    ap.add_argument("--app-url", default="http://localhost:8092",
                    help="app base URL for --mode governed")
    args = ap.parse_args()
    try:
        if args.mode == "governed":
            return asyncio.run(_governed(args.app_url, args.write_id))
        return asyncio.run(_staging(args.nats, args.write_id))
    except Exception as e:  # noqa: BLE001 — CI probe must fail loudly with context
        print(f"::error::write-queue probe failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
