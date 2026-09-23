#!/usr/bin/env python3
"""Write-path canary: kernel (incumbent :8100 vs twin :4100).

Ruling 59f8e2af (Q-A): real-write APPROVED with conditions — one aggregate
per run (id canary-wp-<runid>, never replayed); committed rows are accepted
permanent residue (append-only law); authorities must be REAL classes (the
refusal case uses the documented PG-45000 trigger path — policy rule
'authority.required' denies authority-less transitions; the commit case uses
authority 'system'); SSE negative path asserted on refusals; SSE POSITIVE
path (highest-value evidence) asserted on the commit.

The kernel has NO DELETE — this script's cleanup is verification-only.
"""
from __future__ import annotations

import os
import argparse
import json
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from write_canary_lib import (  # noqa: E402
    Canary, Case, RUN_ID, sse_first_frames, pg, sql_lit, deep_equal,
)

SSE_PATH = "/api/kernel/events/stream"
TRANSITIONS_PATH = "/api/kernel/transitions"


def agg_id(tag: str) -> str:
    return f"canary-wp-{RUN_ID}-{tag}"


def case_validation_400s(canary: Canary, case: Case) -> None:
    """Each missing required field → 400 with the incumbent's message, both twins."""
    base = {
        "event_type": "observation.captured",
        "aggregate_type": "canary-wp",
        "aggregate_id": agg_id("v400"),
        "actor": "engineer-canary",
    }
    for missing in ("event_type", "aggregate_type", "aggregate_id", "actor"):
        body = {k: v for k, v in base.items() if k != missing}
        ab = lambda _t, b=body: b  # noqa: E731
        ra = canary.twin_a().request("POST", TRANSITIONS_PATH, ab(None))
        rb = canary.twin_b().request("POST", TRANSITIONS_PATH, ab(None))
        if ra[0] != 400 or rb[0] != 400:
            raise AssertionError(f"missing {missing}: status A={ra[0]} B={rb[0]}")
        ok, diff = deep_equal(ra[1], rb[1])
        if not ok:
            raise AssertionError(f"missing {missing}: envelope mismatch {diff}")


def case_refusal_403_and_sse_negative(canary: Canary, case: Case) -> None:
    """Authority-less transition → PG 45000 → 403 both twins; SSE emits NO frame."""
    # A side: open the SSE listener BEFORE the refused write
    frames_a: list[bytes] = []
    frames_b: list[bytes] = []

    def listen(port: int, sink: list) -> None:
        try:
            sink.append(sse_first_frames(port, SSE_PATH, seconds=3.0))
        except Exception as e:  # noqa: BLE001
            sink.append(str(e).encode())

    ta = threading.Thread(target=listen, args=(canary.port_a, frames_a))
    tb = threading.Thread(target=listen, args=(canary.port_b, frames_b))
    ta.start(); tb.start()
    time.sleep(0.5)  # let both listeners attach

    body = lambda _t: {  # noqa: E731 — authority deliberately absent
        "event_type": "observation.captured",
        "aggregate_type": "canary-wp",
        "aggregate_id": agg_id("refused"),
        "actor": "engineer-canary",
    }
    ra = canary.twin_a().request("POST", TRANSITIONS_PATH, body(None))
    rb = canary.twin_b().request("POST", TRANSITIONS_PATH, body(None))
    if ra[0] != 403 or rb[0] != 403:
        raise AssertionError(f"refusal status A={ra[0]} B={rb[0]} (expect 403 both)")
    ok, diff = deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"refusal envelope mismatch: {diff}")
    ta.join(); tb.join()
    for name, frames in (("A", frames_a), ("B", frames_b)):
        raw = frames[0] if frames else b""
        text = raw.decode("utf-8", "replace")
        # refused writes must NOT notify: no data frame may appear in the window
        if "data:" in text and '"aggregate_id"' in text:
            raise AssertionError(
                f"SSE NEGATIVE FAILED on {name}: refused write produced a frame"
            )


def case_real_transition_commit_and_sse_positive(canary: Canary, case: Case) -> None:
    """Q-A real write: one marked transition per side; 201 envelopes + DB rows +
    pg_notify positive path (a committed write emits a frame to both listeners)."""
    eid_a, eid_b = agg_id(f"commit-a-{int(time.time())}"), agg_id(f"commit-b-{int(time.time())}")
    frames_a: list[bytes] = []
    frames_b: list[bytes] = []

    def listen(port: int, sink: list) -> None:
        try:
            sink.append(sse_first_frames(port, SSE_PATH, seconds=4.0))
        except Exception as e:  # noqa: BLE001
            sink.append(str(e).encode())

    ta = threading.Thread(target=listen, args=(canary.port_a, frames_a))
    tb = threading.Thread(target=listen, args=(canary.port_b, frames_b))
    ta.start(); tb.start()
    time.sleep(0.5)

    def body_for(eid: str):
        return lambda _t: {
            "event_type": "observation.captured",
            "aggregate_type": "canary-wp",
            "aggregate_id": eid,
            "actor": "engineer-canary",
            "authority": "system",  # real class: passes authority.required
            "payload": {"note": "write-path canary", "run": RUN_ID},
        }

    ra = canary.twin_a().request("POST", TRANSITIONS_PATH, body_for(eid_a)(None))
    rb = canary.twin_b().request("POST", TRANSITIONS_PATH, body_for(eid_b)(None))
    if ra[0] != 201 or rb[0] != 201:
        canary.note_residue(f"commit status A={ra[0]} B={rb[0]} — check partial residue")
        raise AssertionError(f"commit status A={ra[0]} B={rb[0]} (expect 201 both)")
    ok, diff = deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"commit envelope mismatch: {diff}")
    # DB arbiter: both rows exist, event types match, payloads match (ids differ by design)
    q = lambda eid: pg(  # noqa: E731
        f"SELECT event_type, aggregate_type, actor, authority, payload::text "
        f"FROM kernel.transition_event WHERE aggregate_id = {sql_lit(eid)}"
    )
    wa, wb = q(eid_a), q(eid_b)
    if len(wa) != 1 or len(wb) != 1:
        canary.note_residue(f"commit rows: A={len(wa)} B={len(wb)}")
        raise AssertionError("committed transition row missing (append-only surface)")
    ok, diff = deep_equal(
        {k: v for k, v in wa[0].items() if k != "col0"},
        {k: v for k, v in wb[0].items() if k != "col0"},
    )
    if not ok:
        raise AssertionError(f"DB row mismatch: {diff}")
    ta.join(); tb.join()
    fa = (frames_a[0] if frames_a else b"").decode("utf-8", "replace")
    fb = (frames_b[0] if frames_b else b"").decode("utf-8", "replace")
    if '"aggregate_id"' not in fa or eid_a not in fa:
        raise AssertionError("SSE POSITIVE FAILED on A: committed write produced no frame")
    if '"aggregate_id"' not in fb or eid_b not in fb:
        raise AssertionError("SSE POSITIVE FAILED on B: committed write produced no frame")
    # residue note: the ruling accepts these rows as permanent (append-only law)
    canary.note_residue(
        f"ACCEPTED (ruling 59f8e2af Q-A): kernel.transition_event rows "
        f"aggregate_id IN ('{eid_a}', '{eid_b}') are permanent canary residue"
    )


def case_receipt_chain_reads(canary: Canary, case: Case) -> None:
    """Read the receipt chain of the committed events — read-after-write parity."""
    rows = pg(
        f"SELECT event_id FROM kernel.transition_event "
        f"WHERE aggregate_type = 'canary-wp' AND aggregate_id LIKE 'canary-wp-%' "
        f"ORDER BY timestamp DESC LIMIT 1"
    )
    if not rows:
        return  # no committed canary rows yet — nothing to read
    eid = rows[0]["col0"]
    ra = canary.twin_a().request("GET", f"/api/kernel/receipts/{eid}/chain")
    rb = canary.twin_b().request("GET", f"/api/kernel/receipts/{eid}/chain")
    if ra[0] != rb[0]:
        raise AssertionError(f"receipt-chain status A={ra[0]} B={rb[0]}")
    ok, diff = deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"receipt-chain mismatch: {diff}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port-a", type=int, default=8100)
    ap.add_argument("--port-b", type=int, default=4100)
    ap.add_argument("--skip-real-write", action="store_true",
                    help="validation + refusal + SSE-negative only (no committed rows)")
    args = ap.parse_args()

    canary = Canary("kernel", args.port_a, args.port_b)
    canary.cases.extend([
        Case("validation-400s", case_validation_400s),
        Case("refusal-403-sse-negative", case_refusal_403_and_sse_negative),
    ])
    if not args.skip_real_write:
        canary.cases.extend([
            Case("real-commit-sse-positive", case_real_transition_commit_and_sse_positive),
            Case("receipt-chain-read-after-write", case_receipt_chain_reads),
        ])
    return canary.run()


if __name__ == "__main__":
    raise SystemExit(main())
