#!/usr/bin/env python3
"""Postcondition: classify the governed write-queue outcome from durable evidence.

Used by the D5 (phase-2) boot smoke in .github/workflows/nexus-core-image.yml.

Reads the reconciler's two canonical writes for a governed transition-entity
intent:
  resolution.write_queue_applied   — the audit staging row (outcome column)
  resolution.keychain_event_outbox — the durable KeychainEvent (interpreter path)

Terminology per the reconciler contract:
  committed / refused  — seed data present, the interpreter decided on guards
  rejected             — no seed data; the empty interpreter rejects
                         entity-not-found. This is a REAL governed decision
                         (durable event recorded), and is the EXPECTED outcome
                         on the schema-only CI snapshot.
Exits 0 when the intent reached the interpreter and produced any decided
outcome with its durable event; exits 1 with diagnostic context otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import psycopg2

STAGED_DECIDED = ("committed", "refused", "rejected")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL",
                    "postgres://nexus:nexus@localhost:5432/nexus"))
    ap.add_argument("--write-id", required=True)
    ap.add_argument("--timeout", type=int, default=60, help="seconds to wait")
    args = ap.parse_args()

    deadline = time.monotonic() + args.timeout
    conn = None
    while time.monotonic() < deadline:
        try:
            conn = psycopg2.connect(args.dsn, connect_timeout=5)
            break
        except Exception:
            time.sleep(2)
    if conn is None:
        print("::error::postcondition could not reach PostgreSQL")
        return 1

    try:
        with conn.cursor() as cur:
            while time.monotonic() < deadline:
                cur.execute(
                    "SELECT outcome FROM resolution.write_queue_applied "
                    "WHERE write_id = %s",
                    (args.write_id,))
                staged = cur.fetchone()
                cur.execute(
                    "SELECT outcome, event_kind, aggregate_id, source_event_id "
                    "FROM resolution.keychain_event_outbox "
                    "WHERE source_event_id = %s",
                    (args.write_id,))
                event = cur.fetchone()
                if staged and event:
                    result = {
                        "writeId": args.write_id,
                        "staged_outcome": staged[0],
                        "outbox_event": {
                            "outcome": event[0],
                            "event_kind": event[1],
                            "aggregate_id": event[2],
                            "source_event_id": event[3],
                        },
                    }
                    print(json.dumps(result))
                    if staged[0] in STAGED_DECIDED:
                        if staged[0] == "rejected":
                            print("outcome: rejected — no seed data; the empty "
                                  "interpreter's deterministic entity-not-found "
                                  "decision, durably recorded (expected on the "
                                  "schema-only snapshot)")
                        return 0
                    print("::error::intent reached the interpreter but staged "
                          f"outcome '{staged[0]}' is not a decided state")
                    return 1
                time.sleep(2)
            print(f"::error::governed intent {args.write_id} never produced a "
                  "staging row + durable outbox event within the timeout")
            return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
