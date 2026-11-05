#!/usr/bin/env python3
"""Silent-success checker: harness runs that report completed and leave no record.

A run's `completed` state says the model's stream ended on a natural stop (see
typescript/harness-srv/src/terminal.ts). It does NOT say the agent wrote
anything down. Those came apart repeatedly on 2026-11-04/05: tester runs
reported `completed` after 771s and 768s with nothing posted, and the two
attestations that finally landed only did so after a human noticed the gap by
hand.

This check answers one question per run: *did the role post an agent record
anywhere inside the run's own window?* A completed run with no record is a
finding — the work may have happened in the working tree, but the durable trail
that every role is required to leave (AGENTS.md R1/R2) is missing.

Input is the durable event log, not memory: `cascade.events` rows of type
`harness.completed` carry `role` and `duration_ms`, so the window is
[completed_at - duration_ms, completed_at + grace]. Records are matched by
role alone — agent records do not carry a job id — which is deliberate: a
second record from the same role inside the window also satisfies the check,
and false negatives are cheaper here than false alarms.

Duration is NOT a filter by default (`--min-duration 0`). The case this check
exists for — job 2b5c34ae, tester, 2026-11-04 — exited 0 after 35 SECONDS with
nothing written; a 60s threshold would have hidden it, and hiding the short
failures is how the long ones survived unnoticed. An ad-hoc 13s probe that
answers "pong" and files nothing does appear as a finding: one line with its
duration, triaged in seconds. Use `--min-duration 60` when sweeping a noisy
window rather than an audit window.

Usage:
    check_harness_silent_success.py [--since ISO] [--hours N]
                                    [--min-duration 0] [--grace 180]

Exit codes: 0 = clean, 1 = findings, 2 = tool/environment error (file nothing).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

DSN = os.environ.get(
    "SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus"
)

DEFAULT_MIN_DURATION_S = 0
DEFAULT_GRACE_S = 180


def silent_successes(
    completed: list[dict],
    records_by_role: dict[str, list[datetime]],
    min_duration_ms: int = DEFAULT_MIN_DURATION_S * 1000,
    grace_ms: int = DEFAULT_GRACE_S * 1000,
) -> list[dict]:
    """Pure classification: which completed runs left no record behind.

    `completed` items: {job_id, role, completed_at (aware datetime), duration_ms,
    state, detail?}. `records_by_role`: role -> record timestamps (aware).
    Returns findings in completion order. Kept free of DB imports so it can be
    tested without a driver or a database (see bin/tests/).
    """
    findings: list[dict] = []
    for run in completed:
        role = run.get("role") or ""
        duration_ms = int(run.get("duration_ms") or 0)
        completed_at = run["completed_at"]
        if not role:
            continue
        if duration_ms < min_duration_ms:
            continue
        start = completed_at - timedelta(milliseconds=duration_ms) - timedelta(
            milliseconds=grace_ms
        )
        end = completed_at + timedelta(milliseconds=grace_ms)
        stamps = records_by_role.get(role) or []
        if any(start <= ts <= end for ts in stamps):
            continue
        findings.append(
            {
                "job_id": run.get("job_id"),
                "role": role,
                "completed_at": completed_at.isoformat(),
                "duration_ms": duration_ms,
                "window": [start.isoformat(), end.isoformat()],
                "state": run.get("state") or "completed",
                "detail": run.get("detail") or "",
            }
        )
    return findings


def _parse_ts(value: str) -> datetime:
    """Parse an ISO timestamp into an aware UTC datetime."""
    ts = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def scan(
    since: datetime,
    min_duration_ms: int,
    grace_ms: int,
) -> list[dict]:
    """Read the event log and the record trail, then classify."""
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "psycopg2 is required to scan the database; "
            "install psycopg2 or run against a host that has it"
        ) from exc

    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select aggregate_id, payload, event_timestamp
            from cascade.events
            where event_type = 'harness.completed'
              and event_timestamp > %(since)s
            order by event_timestamp asc
            """,
            {"since": since},
        )
        completed = [
            {
                "job_id": str(row[0]) if row[0] else None,
                "role": (row[1] or {}).get("role") or "",
                "completed_at": row[2].astimezone(timezone.utc),
                "duration_ms": int((row[1] or {}).get("duration_ms") or 0),
                "state": (row[1] or {}).get("state") or "completed",
                "detail": ((row[1] or {}).get("detail") or "")[:200],
            }
            for row in cur.fetchall()
        ]

        roles = sorted({c["role"] for c in completed if c["role"]})
        records_by_role: dict[str, list[datetime]] = {r: [] for r in roles}
        if roles:
            # One query for the whole sweep: every record by the roles under
            # study, across a window wide enough for the longest run.
            span = max((c["duration_ms"] for c in completed), default=0)
            earliest = since - timedelta(milliseconds=span) - timedelta(
                milliseconds=grace_ms
            )
            cur.execute(
                """
                select role, created_at
                from nebula.agent_records
                where role = ANY(%(roles)s)
                  and created_at > %(since)s
                """,
                {"roles": roles, "since": earliest},
            )
            for role, created_at in cur.fetchall():
                records_by_role.setdefault(role, []).append(
                    created_at.astimezone(timezone.utc)
                )

    return silent_successes(completed, records_by_role, min_duration_ms, grace_ms)


def main() -> int:
    args = sys.argv[1:]
    since = None
    if "--since" in args:
        since = _parse_ts(args[args.index("--since") + 1])
    elif "--hours" in args:
        since = datetime.now(timezone.utc) - timedelta(
            hours=float(args[args.index("--hours") + 1])
        )
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    min_duration_ms = (
        int(float(args[args.index("--min-duration") + 1]) * 1000)
        if "--min-duration" in args
        else DEFAULT_MIN_DURATION_S * 1000
    )
    grace_ms = (
        int(float(args[args.index("--grace") + 1]) * 1000)
        if "--grace" in args
        else DEFAULT_GRACE_S * 1000
    )

    try:
        findings = scan(since, min_duration_ms, grace_ms)
    except Exception as exc:  # noqa: BLE001 — tool error, caller decides
        print(f"tool error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "since": since.isoformat(),
                "min_duration_ms": min_duration_ms,
                "grace_ms": grace_ms,
                "count": len(findings),
                "findings": findings,
            },
            indent=2,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
