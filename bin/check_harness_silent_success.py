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
[completed_at - duration_ms, completed_at + grace].

Matching, strongest evidence first (V210):

1. **job id** — a record stamped with this run's `harness_job_id` clears it,
   wherever in the window it landed. The harness exports `HARNESS_JOB_ID` to
   every job child, so the canonical recorder and any shell writer stamp it
   without the model having to pass anything.
2. **window** — a record from the role inside the window that carries NO job
   id still clears the run. Rows written before V210, and rows written through
   MCP without the field, are unattributed; refusing to count them would turn
   every legacy run into a finding.

A record stamped with a DIFFERENT job id never clears a run, even inside its
window. That is the defect this column exists for: the old role+window match
was cleared by any sibling record from the same role, which is exactly how a
silent run stays invisible. Findings that had records in the window attributed
elsewhere list those ids under `attributed_elsewhere`, so "no record of mine"
and "records of other runs" are distinguishable in the report.

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


def _record(entry) -> tuple[datetime, str | None]:
    """Normalize one record entry to (created_at, harness_job_id).

    Accepts the two shapes callers use: a bare aware datetime (unattributed,
    the pre-V210 shape the existing tests and older callers pass) and a dict
    {created_at, harness_job_id?} as scan() builds from the DB.
    """
    if isinstance(entry, datetime):
        return entry, None
    return entry["created_at"], entry.get("harness_job_id")


def _classify(
    completed: list[dict],
    records_by_role: dict[str, list],
    min_duration_ms: int,
    grace_ms: int,
) -> list[dict]:
    """One result per run, satisfied or not, with the evidence that decided it."""
    results: list[dict] = []
    for run in completed:
        role = run.get("role") or ""
        duration_ms = int(run.get("duration_ms") or 0)
        completed_at = run["completed_at"]
        if not role:
            continue
        if duration_ms < min_duration_ms:
            continue
        job_id = run.get("job_id")
        start = completed_at - timedelta(milliseconds=duration_ms) - timedelta(
            milliseconds=grace_ms
        )
        end = completed_at + timedelta(milliseconds=grace_ms)
        records = [_record(e) for e in (records_by_role.get(role) or [])]
        if job_id and any(run_id == job_id for _, run_id in records):
            results.append({"run": run, "satisfied_by": "job-id"})
            continue
        in_window = [(ts, rid) for ts, rid in records if start <= ts <= end]
        if any(rid is None for _, rid in in_window):
            results.append({"run": run, "satisfied_by": "window"})
            continue
        results.append(
            {
                "run": run,
                "satisfied_by": None,
                "attributed_elsewhere": sorted({rid for _, rid in in_window if rid}),
            }
        )
    return results


def silent_successes(
    completed: list[dict],
    records_by_role: dict[str, list],
    min_duration_ms: int = DEFAULT_MIN_DURATION_S * 1000,
    grace_ms: int = DEFAULT_GRACE_S * 1000,
) -> list[dict]:
    """Pure classification: which completed runs left no record behind.

    `completed` items: {job_id, role, completed_at (aware datetime), duration_ms,
    state, detail?}. `records_by_role`: role -> entries, each either an aware
    datetime (unattributed) or {created_at, harness_job_id}.
    Returns findings in completion order. Kept free of DB imports so it can be
    tested without a driver or a database (see bin/tests/).
    """
    findings: list[dict] = []
    for result in _classify(completed, records_by_role, min_duration_ms, grace_ms):
        if result["satisfied_by"]:
            continue
        run = result["run"]
        duration_ms = int(run.get("duration_ms") or 0)
        completed_at = run["completed_at"]
        start = completed_at - timedelta(milliseconds=duration_ms) - timedelta(
            milliseconds=grace_ms
        )
        end = completed_at + timedelta(milliseconds=grace_ms)
        finding = {
            "job_id": run.get("job_id"),
            "role": run.get("role"),
            "completed_at": completed_at.isoformat(),
            "duration_ms": duration_ms,
            "window": [start.isoformat(), end.isoformat()],
            "state": run.get("state") or "completed",
            "detail": run.get("detail") or "",
        }
        if result.get("attributed_elsewhere"):
            finding["attributed_elsewhere"] = result["attributed_elsewhere"]
        findings.append(finding)
    return findings


def clearance(
    completed: list[dict],
    records_by_role: dict[str, list],
    min_duration_ms: int = DEFAULT_MIN_DURATION_S * 1000,
    grace_ms: int = DEFAULT_GRACE_S * 1000,
) -> dict[str, int]:
    """How many runs each kind of evidence cleared: {job-id, window, none}.

    Reported alongside the findings so a clean sweep says WHICH kind of clean it
    is. "0 findings, 0 attributed" is a weaker statement than "0 findings, 12
    matched by job id", and the report should not blur the two.
    """
    counts = {"job-id": 0, "window": 0, "none": 0}
    for result in _classify(completed, records_by_role, min_duration_ms, grace_ms):
        counts[result["satisfied_by"] or "none"] += 1
    return counts


def _parse_ts(value: str) -> datetime:
    """Parse an ISO timestamp into an aware UTC datetime."""
    ts = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_job_id_column(cur) -> bool:
    """True when the DB has V210's column.

    A checkout can run against a database restored from before the migration
    (or against a staging copy). Asking first keeps the sweep working there:
    every record is simply unattributed, which is the pre-V210 behaviour, and
    the report says so via clearance counts instead of crashing.
    """
    cur.execute(
        """
        select exists (
          select 1 from information_schema.columns
          where table_schema = 'nebula' and table_name = 'agent_records'
            and column_name = 'harness_job_id'
        )
        """
    )
    return bool(cur.fetchone()[0])


def scan(
    since: datetime,
    min_duration_ms: int,
    grace_ms: int,
) -> tuple[list[dict], dict[str, int]]:
    """Read the event log and the record trail, then classify.

    Returns (findings, clearance counts).
    """
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
        records_by_role: dict[str, list[dict]] = {r: [] for r in roles}
        attributed = _has_job_id_column(cur)
        if roles:
            # One query for the whole sweep: every record by the roles under
            # study, across a window wide enough for the longest run.
            span = max((c["duration_ms"] for c in completed), default=0)
            earliest = since - timedelta(milliseconds=span) - timedelta(
                milliseconds=grace_ms
            )
            cur.execute(
                f"""
                select role, created_at, {"harness_job_id" if attributed else "null"}
                from nebula.agent_records
                where role = ANY(%(roles)s)
                  and created_at > %(since)s
                """,
                {"roles": roles, "since": earliest},
            )
            for role, created_at, job_id in cur.fetchall():
                records_by_role.setdefault(role, []).append(
                    {
                        "created_at": created_at.astimezone(timezone.utc),
                        "harness_job_id": job_id,
                    }
                )

    findings = silent_successes(completed, records_by_role, min_duration_ms, grace_ms)
    counts = clearance(completed, records_by_role, min_duration_ms, grace_ms)
    counts["attribution_available"] = 1 if attributed else 0
    return findings, counts


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
        findings, counts = scan(since, min_duration_ms, grace_ms)
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
                "clearance": counts,
                "findings": findings,
            },
            indent=2,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
