#!/usr/bin/env python3
"""Transition-event attestation check (CD-2 design amendment).

Every terminal ticket MUST carry at least one kernel.transition_event row
describing how it reached its terminal state (ADR-016 / ruling 1867c88d:
"every generation must receive a durable expiry transition"). A terminal
ticket with NO events means state was mutated without an audit trail —
a silent write path, an out-of-band mutation, or a projection gap. This
checker measures that join, read-only, so the class surfaces automatically
instead of being discovered by hand (as it nearly wasn't: record ae18bba7
— the original gen-2 "zero events" finding was itself a failed query
misread as an empty result).

Semantics:
  - FAIL (exit 1) iff any UNATTESTED terminal ticket exists newer than the
    adoption cutoff (see --since). Historical silence predating the emission
    standard is counted and reported as HISTORICAL, not failed — a backfill
    decision belongs to the architect, not to a checker exit code.
  - WAIVED (ruling 6b42dd3f): the enumerated rows in
    bin/transition-attestation-waivers.json are attested by their row-level
    closure evidence instead of an event, because they closed before the
    receipt-advance emission fix (PR #560). The waiver is NARROW by ruling:
    a waived id that has meanwhile GROWN an event is a contradiction
    (FATAL exit 2 — it would mean event history and the waiver record
    disagree), and the list never suppresses a ticket not named in it.
  - Any query error is FATAL: absence of evidence must never be concluded
    from a failing probe (the ae18bba7 lesson, enforced in code).
  - Green-heartbeat contract (PRs #527/#540 parity): the wrapper
    (bin/transition_attestation_wrap.py, deployed with the timer) files a
    green record weekly / on commissioning and a drift record on every
    non-clean run.

Run:
  CONDUIT_PG_DSN='postgresql://...' python3 bin/check_transition_attestation.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Adoption cutoff: emissions became a standard with the CD-2 arc. Tickets
# closed before this date are HISTORICAL by definition (see design §4.1);
# nothing before it fails the check. 2026-09-22 = the walk-through date
# when the fleet began enforcing durable transitions per generation.
DEFAULT_CUTOVER = "2026-09-22T00:00:00Z"

# Ruled waiver list (architect ruling 6b42dd3f): enumerated historical rows
# attested by closure evidence instead of events. Resolved next to this
# script so the checker works from any cwd.
WAIVER_FILE = Path(__file__).resolve().parent / "transition-attestation-waivers.json"


def load_waiver_ids(path: Path = WAIVER_FILE) -> dict[str, dict]:
    """Pure-ish: read the ruled waiver file into {ticket_id: entry}.

    A malformed or empty-but-declared waiver file is FATAL at the caller —
    an audit exception list that silently reads as empty would be a
    standing suppression hole (the exact thing ruling 6b42dd3f forbids).
    """
    data = json.loads(path.read_text())
    waivers = data.get("waivers")
    if not isinstance(waivers, list):
        raise ValueError(f"{path}: 'waivers' must be a list")
    out: dict[str, dict] = {}
    for w in waivers:
        tid = w.get("ticket_id")
        if not tid:
            raise ValueError(f"{path}: waiver entry missing ticket_id")
        if tid in out:
            raise ValueError(f"{path}: duplicate waiver for {tid}")
        out[tid] = w
    return out

TERMINAL_STATUSES = ("cancelled", "superseded", "abandoned", "expired", "failed", "completed")


def classify(events_per_ticket: dict[str, int]) -> dict[str, list[str]]:
    """Pure classification: map ticket_id -> event count into buckets.

    Returns {"unattested": [...], "attested": [...]} — stable order,
    unattested sorted for deterministic output.
    """
    unattested = sorted(tid for tid, n in events_per_ticket.items() if n == 0)
    attested = sorted(tid for tid, n in events_per_ticket.items() if n > 0)
    return {"unattested": unattested, "attested": attested}


def apply_waivers(
    counts: dict[str, int], waiver_ids: set[str]
) -> tuple[set[str], set[str]]:
    """Pure: split the waived ids into (honored, contradicted).

    honored       — waived id present with zero events (the ruled state).
    contradicted  — waived id that has GROWN an event since the ruling:
                    event history and the waiver record now disagree, which
                    is an audit contradiction, not a clean state.
    Waived ids absent from `counts` are simply not scanned this run
    (e.g. if a row were archived out of the terminal set) and are ignored.
    """
    honored = {tid for tid in waiver_ids if counts.get(tid) == 0}
    contradicted = {tid for tid in waiver_ids if counts.get(tid, 0) > 0}
    return honored, contradicted


def verdict_from_counts(unattested_recent: int, historical: int) -> str:
    """Pure: exit verdict from bucket counts. CLEAN | DRIFT."""
    return "CLEAN" if unattested_recent == 0 else "DRIFT"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dsn", default=os.environ.get("CONDUIT_PG_DSN", ""),
                    help="PostgreSQL DSN (default $CONDUIT_PG_DSN)")
    ap.add_argument("--since", default=DEFAULT_CUTOVER,
                    help=f"adoption cutoff ISO (default {DEFAULT_CUTOVER}); "
                         f"terminal tickets closed after this WITHOUT events fail")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if not args.dsn:
        print("ERROR: --dsn or CONDUIT_PG_DSN required", file=sys.stderr)
        return 2

    try:
        # Pass the path explicitly (a def-time default would freeze the
        # module constant and defeat test monkeypatching).
        waiver_ids = set(load_waiver_ids(WAIVER_FILE))
    except Exception as exc:  # noqa: BLE001 — fail loudly, never "empty"
        print(f"ERROR: waiver list unusable: {exc}", file=sys.stderr)
        return 2

    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not available", file=sys.stderr)
        return 2

    try:
        with psycopg2.connect(args.dsn) as conn, conn.cursor() as cur:
            # Terminal tickets closed at/after the cutoff, with event counts.
            # The join is the measurement; no per-ticket probes that could
            # fail independently and be misread (ae18bba7).
            cur.execute(
                """
                SELECT t.id, t.status, count(te.id) AS event_count
                FROM vision.tickets t
                LEFT JOIN kernel.transition_event te
                  ON te.aggregate_type = 'ticket' AND te.aggregate_id = t.id
                WHERE t.status = ANY(%s)
                  AND t.closed_at IS NOT NULL
                  AND t.closed_at >= %s::timestamptz
                GROUP BY t.id, t.status
                """,
                (list(TERMINAL_STATUSES), args.since),
            )
            rows = cur.fetchall()

            # Historical mass (pre-cutoff silence) — counted, never failed.
            cur.execute(
                """
                SELECT count(*) FROM vision.tickets t
                WHERE t.status = ANY(%s)
                  AND t.closed_at IS NOT NULL
                  AND t.closed_at < %s::timestamptz
                  AND NOT EXISTS (
                    SELECT 1 FROM kernel.transition_event te
                    WHERE te.aggregate_type = 'ticket' AND te.aggregate_id = t.id)
                """,
                (list(TERMINAL_STATUSES), args.since),
            )
            historical = cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001 — fail loudly, never "empty"
        print(f"ERROR: attestation query failed: {exc}", file=sys.stderr)
        return 2

    counts = {tid: int(n) for tid, _status, n in rows}
    buckets = classify(counts)
    honored, contradicted = apply_waivers(counts, waiver_ids)
    unattested_recent = len([t for t in buckets["unattested"] if t not in waiver_ids])
    if contradicted:
        # A ruled exception row now carries an event: the waiver record and
        # event history disagree. Fatal, not DRIFT and not CLEAN.
        print("ERROR: waived tickets carry transition events — audit "
              "contradiction, investigate before re-running: "
              + ", ".join(sorted(contradicted)), file=sys.stderr)
        return 2
    verdict = verdict_from_counts(unattested_recent, historical)

    result = {
        "verdict": verdict,
        "cutoff": args.since,
        "terminal_scanned": len(rows),
        "unattested_recent": unattested_recent,
        "historical_silent": historical,
        "waivers_honored": sorted(honored),
        "waivers_contradicted": sorted(contradicted),
        "by_status": {},
        "unattested_ids": [t for t in buckets["unattested"] if t not in waiver_ids][:50],
        "unattested_truncated": max(0, unattested_recent - 50),
    }
    status_counts: dict[str, int] = {}
    unattested_set = set(buckets["unattested"]) - waiver_ids
    for tid, status, _n in rows:
        if tid in unattested_set:
            status_counts[status] = status_counts.get(status, 0) + 1
    result["by_status"] = dict(sorted(status_counts.items()))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"transition-event attestation: {verdict}")
        print(f"  terminal tickets scanned (since {args.since}): {len(rows)}")
        print(f"  unattested (FAIL class):        {unattested_recent}")
        print(f"  historical silent (not failed): {historical}")
        if honored:
            print(f"  waived (ruling 6b42dd3f):       {len(honored)}")
        if status_counts:
            print("  unattested by status: " + ", ".join(
                f"{k}={v}" for k, v in result["by_status"].items()))
        for tid in result["unattested_ids"][:10]:
            print(f"    - {tid}")
        if result["unattested_truncated"]:
            print(f"    … and {result['unattested_truncated']} more")

    return 0 if verdict == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
