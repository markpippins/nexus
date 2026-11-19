#!/usr/bin/env python3
"""W-B3 (plan 8261675): generation-aware sweep-disposition attestations.

Read-only against `nexus`. Produces one log row per (plan_id, role, generation)
using the D1 §7.2 window spine, joins expiry events by ticket id, and computes
S2/S3/S4 dispositions in the log. Nothing is written back to a ticket.

Usage:
    bin/wb3_sweep_log.py [--out PATH] [--waivers PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "docs/promotion-flow/evidence/wb3-sweep-log-8261675.json"
DEFAULT_WAIVERS = REPO / "bin/transition-attestation-waivers.json"
DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"

RULING_REF = "6b42dd3f-ae62-4abf-8cce-fdc01c41032e"
COHORT = "wb3_pre_fix_expired_generation"

TERMINAL_OUTCOMES = {"completed", "failed", "cancelled", "closed"}
ATTENTION_LOST = {"expired", "stale"}
ACTIVE = {"open", "claimed"}
KNOWN_STATUSES = TERMINAL_OUTCOMES | ATTENTION_LOST | ACTIVE

DISPOSITIONS = (
    "expired_with_event",
    "expired_without_event_waived",
    "open_past_expiry_disposed",
    "no_disposition_required",
)

# Blueprint stamp (R-F12, 2026-11-17). Deltas are reported, never absorbed.
BASELINES = {
    "s1": {"rows": 843, "plans": 406, "null_plan_id": 0},
    "s2": {"disposition_required": 138},
    "s3": {"event_absent_expired": 89},
    "s4": {"ticket": 468, "expiry_event": 49, "status_fallback": 326},
    "s5": {"live_open_generations": 0},
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc).isoformat()


def _millis(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    return int(datetime.fromisoformat(str(value)).timestamp() * 1000)


def is_open_generation_status(status: str) -> bool:
    """D1 §7.2: open generations are open/claimed/stale."""
    return status in ACTIVE or status == "stale"


def is_disposition_required(status: str, expires_at: Any, swept_at: Any) -> bool:
    """S2: expired/stale always; open/claimed only when already past expiry."""
    if status in ATTENTION_LOST:
        return True
    if status not in ACTIVE:
        return False
    expires = _iso(expires_at)
    if expires is None:
        return False
    return _millis(expires) < _millis(swept_at)


def closure_reason_source(closure_reason: str | None, has_expiry_event: bool) -> str:
    """S4 precedence: ticket reason, then expiry event, then status fallback."""
    if closure_reason is not None and closure_reason != "":
        return "ticket"
    if has_expiry_event:
        return "expiry_event"
    return "status_fallback"


def closure_reason_effective(status: str, closure_reason: str | None, has_expiry_event: bool) -> str:
    source = closure_reason_source(closure_reason, has_expiry_event)
    if source == "ticket":
        return str(closure_reason)
    if source == "expiry_event":
        return "expiry_detection"
    return f"status:{status}"


def derive_disposition(
    status: str,
    expires_at: Any,
    has_expiry_event: bool,
    swept_at: Any,
) -> str:
    """S2/S3. Fails closed on unknown status; S3 waiver check is done by caller."""
    if status not in KNOWN_STATUSES:
        raise ValueError(f"unknown ticket status (W-B2 census forbids implicit buckets): {status}")
    if status == "expired":
        return "expired_with_event" if has_expiry_event else "expired_without_event_waived"
    if is_open_generation_status(status) and is_disposition_required(status, expires_at, swept_at):
        return "open_past_expiry_disposed"
    return "no_disposition_required"


def load_waivers(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ruling = data.get("ruling", {}).get("decision_record_id")
    if ruling != RULING_REF:
        raise ValueError(f"waiver registry ruling mismatch: {ruling!r} != {RULING_REF!r}")
    cohort = data.get("cohorts", {}).get(COHORT)
    if not cohort or cohort.get("ruling_ref") != RULING_REF:
        raise ValueError(f"missing/invalid waiver cohort {COHORT}")
    rows = [w for w in data["waivers"] if w.get("cohort") == COHORT]
    if len(rows) != cohort.get("count") or len(rows) != BASELINES["s3"]["event_absent_expired"]:
        raise ValueError(
            f"waiver cohort row count mismatch: registry={len(rows)}, "
            f"declared={cohort.get('count')}, baseline={BASELINES['s3']['event_absent_expired']}"
        )
    out: dict[str, str] = {}
    for row in rows:
        ref = row.get("waiver_ref")
        if ref != RULING_REF:
            raise ValueError(f"waiver {row.get('ticket_id')} has invalid waiver_ref {ref!r}")
        out[row["ticket_id"]] = ref
    return out


SPINE_SQL = """
WITH events AS (
    SELECT aggregate_id AS ticket_id,
           count(*) FILTER (WHERE payload->>'reason' = 'expiry_detection')::int AS expiry_events
      FROM kernel.transition_event
     GROUP BY aggregate_id
), ranked AS (
    SELECT t.plan_id,
           t.role,
           t.id AS ticket_id,
           t.status,
           t.created_at,
           t.expires_at,
           t.closed_at,
           t.closure_reason,
           row_number() OVER (
               PARTITION BY t.plan_id, t.role
               ORDER BY t.created_at, t.id
           ) AS generation,
           count(*) OVER (PARTITION BY t.plan_id, t.role) AS generations_total
      FROM vision.tickets t
     WHERE t.plan_id IS NOT NULL
)
SELECT r.plan_id, r.role, r.generation, r.generations_total, r.ticket_id, r.status,
       r.created_at, r.expires_at, r.closed_at, r.closure_reason,
       COALESCE(e.expiry_events, 0) AS expiry_events
  FROM ranked r
  LEFT JOIN events e ON e.ticket_id = r.ticket_id
 ORDER BY r.plan_id, r.role, r.generation
"""


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def build_artifact(dsn: str, waivers_path: Path) -> dict[str, Any]:
    waiver_map = load_waivers(waivers_path)
    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT now() AS swept_at")
            swept_at = cur.fetchone()["swept_at"]
            cur.execute(SPINE_SQL)
            spine = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT count(*)::int AS n, count(plan_id)::int AS with_plan FROM vision.tickets")
            live = cur.fetchone()
            cur.execute("SELECT count(*)::int AS n FROM vision.tickets WHERE plan_id IS NULL")
            null_plan_id = cur.fetchone()["n"]
            cur.execute("SELECT status, count(*)::int AS n FROM vision.tickets GROUP BY status ORDER BY status")
            status_census = {r["status"]: r["n"] for r in cur.fetchall()}
    finally:
        conn.close()

    rows: list[dict[str, Any]] = []
    for src in spine:
        status = src["status"]
        has_event = int(src["expiry_events"]) > 0
        waiver_ref = waiver_map.get(src["ticket_id"])
        disposition = derive_disposition(status, src["expires_at"], has_event, swept_at)
        if disposition == "expired_without_event_waived" and not waiver_ref:
            raise ValueError(
                f"Unwaived event-absent expiry (S3): ticket {src['ticket_id']} has no "
                "expiry_detection event and no architect waiver_ref"
            )
        stamped_waiver = waiver_ref if disposition == "expired_without_event_waived" else None
        rows.append(
            {
                "plan_id": src["plan_id"],
                "role": src["role"],
                "generation": int(src["generation"]),
                "generations_total": int(src["generations_total"]),
                "ticket_id": src["ticket_id"],
                "status": status,
                "is_open_generation": is_open_generation_status(status),
                "is_newest": int(src["generation"]) == int(src["generations_total"]),
                "created_at": _iso(src["created_at"]),
                "expires_at": _iso(src["expires_at"]),
                "closed_at": _iso(src["closed_at"]),
                "closure_reason": src["closure_reason"],
                "reason_source": closure_reason_source(src["closure_reason"], has_event),
                "closure_reason_effective": closure_reason_effective(
                    status, src["closure_reason"], has_event
                ),
                "sweep_disposition": disposition,
                "disposition_required": is_disposition_required(
                    status, src["expires_at"], swept_at
                ),
                "waiver_ref": stamped_waiver,
            }
        )

    keys = [(r["plan_id"], r["role"], r["generation"]) for r in rows]
    duplicate_keys = len(keys) - len(set(keys))
    live_keys = {r["ticket_id"] for r in spine}
    log_keys = {r["ticket_id"] for r in rows}
    unlogged = len(live_keys - log_keys)
    extra = len(log_keys - live_keys)
    plans = len({r["plan_id"] for r in rows})

    disp_census = Counter(r["sweep_disposition"] for r in rows)
    reason_census = Counter(r["reason_source"] for r in rows)
    disp_by_status: dict[str, dict[str, int]] = defaultdict(dict)
    for status in sorted(status_census):
        by_disp = Counter(r["sweep_disposition"] for r in rows if r["status"] == status)
        disp_by_status[status] = dict(sorted(by_disp.items()))
    open_rows = [r for r in rows if r["is_open_generation"]]
    open_past = [r for r in open_rows if r["disposition_required"]]

    baseline_reasons = BASELINES["s4"]
    return {
        "artifact": "WB3-SWEEP-LOG",
        "schema": "wb3-sweep-log/1",
        "plan_ref": "8261675",
        "work_request_id": os.environ.get("WB3_WORK_REQUEST_ID"),
        "attempt_id": os.environ.get("WB3_ATTEMPT_ID"),
        "session_id": os.environ.get("WB3_SESSION_ID"),
        "database": "nexus",
        "source_reference": "vision.tickets + kernel.transition_event",
        "verification_owner": "planner",
        "swept_at": _iso(swept_at),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "derivation": {
            "generation": "row_number() OVER (PARTITION BY plan_id, role ORDER BY created_at, id) — the D1 §7.2 spine, shared with W-B6",
            "generations_total": "count(*) OVER (PARTITION BY plan_id, role)",
            "expiry_event_join": "kernel.transition_event.aggregate_id = vision.tickets.id AND payload->>'reason' = 'expiry_detection'",
            "closure_reason_effective": "coalesce(ticket.closure_reason, 'expiry_detection' when an expiry event exists, 'status:'||status) — computed in this log, never written back to the ticket",
            "disposition_rules": "expired + event → expired_with_event; expired + no event → expired_without_event_waived (waiver_ref required); open/claimed/stale with expires_at < swept_at → open_past_expiry_disposed; everything else → no_disposition_required",
            "spine_sql": " ".join(SPINE_SQL.split()),
        },
        "baselines": BASELINES,
        "counts": {
            "live_rows": live["n"],
            "logged_rows": len(rows),
            "plans": plans,
            "null_plan_id": null_plan_id,
            "duplicate_keys": duplicate_keys,
            "unlogged_rows": unlogged,
            "extra_rows": extra,
            "pass": (
                live["n"] == live["with_plan"] == len(rows)
                and null_plan_id == 0
                and duplicate_keys == 0
                and unlogged == 0
                and extra == 0
            ),
        },
        "status_census": status_census,
        "disposition_census": {d: disp_census.get(d, 0) for d in DISPOSITIONS},
        "disposition_by_status": dict(disp_by_status),
        "reason_census": {
            "ticket": reason_census.get("ticket", 0),
            "expiry_event": reason_census.get("expiry_event", 0),
            "status_fallback": reason_census.get("status_fallback", 0),
        },
        "deltas_vs_baseline": {
            "rows": live["n"] - BASELINES["s1"]["rows"],
            "plans": plans - BASELINES["s1"]["plans"],
            "disposition_required": sum(
                1 for r in rows if r["disposition_required"]
            )
            - BASELINES["s2"]["disposition_required"],
            "event_absent_expired": disp_census.get("expired_without_event_waived", 0)
            - BASELINES["s3"]["event_absent_expired"],
            "reason": {
                key: reason_census.get(key, 0) - baseline_reasons[key]
                for key in ("ticket", "expiry_event", "status_fallback")
            },
            "note": "Baselines are the blueprint's 2026-11-17 stamp (R-F12). Ticket rows/plans move with lane history; each delta is reported, never silently absorbed.",
        },
        "waivers": {
            "ruling_ref": RULING_REF,
            "ruling_title": "RULING: waive pre-fix terminal gaps; never synthesize historical transition events",
            "surface": "bin/transition-attestation-waivers.json",
            "cohort": COHORT,
            "rows": len(waiver_map),
            "scope": "expired generations whose expires_at lies in 2026-06-18..2026-07-26, before the emission fix (PR #560); no row created after the fix is admitted, and no transition event was synthesized",
            "unwaived": sum(
                1
                for r in rows
                if r["sweep_disposition"] == "expired_without_event_waived"
                and not r["waiver_ref"]
            ),
        },
        "live_open_generations": len(open_rows),
        "live_open_generations_detail": {
            "swept_at": _iso(swept_at),
            "live_open_generations": len(open_rows),
            "live_open_generations_past_expiry": len(open_past),
            "live_open_generation_rows": [
                {
                    "plan_id": r["plan_id"],
                    "role": r["role"],
                    "generation": r["generation"],
                    "ticket_id": r["ticket_id"],
                    "status": r["status"],
                    "expires_at": r["expires_at"],
                }
                for r in open_rows
            ],
        },
        "assertions": {
            "S1_one_row_per_generation": {
                "pass": bool(rows) and duplicate_keys == 0 and unlogged == 0 and extra == 0
            },
            "S2_disposition_vocabulary": {
                "pass": all(r["sweep_disposition"] in DISPOSITIONS for r in rows),
                "nulls": 0,
                "out_of_vocabulary": 0,
            },
            "S3_waiver_refs": {
                "pass": all(
                    r["waiver_ref"] is not None
                    for r in rows
                    if r["sweep_disposition"] == "expired_without_event_waived"
                )
            },
            "S4_reason_provenance": {
                "pass": all(
                    r["reason_source"] in {"ticket", "expiry_event", "status_fallback"}
                    for r in rows
                )
            },
            "S5_live_open_generations": {
                "pass": len(open_past) == 0,
                "note": "live_open_generations is reported verbatim; the D1 baseline of 0 is not forced when lane tickets are open inside their window",
            },
        },
        "generations": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--waivers", type=Path, default=DEFAULT_WAIVERS)
    args = parser.parse_args()

    dsn = os.environ.get("CONDUIT_PG_DSN", DEFAULT_DSN)
    artifact = build_artifact(dsn, args.waivers)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(args.out),
                "swept_at": artifact["swept_at"],
                "counts": artifact["counts"],
                "disposition_census": artifact["disposition_census"],
                "reason_census": artifact["reason_census"],
                "live_open_generations": artifact["live_open_generations"],
                "assertions": {
                    k: v["pass"] for k, v in artifact["assertions"].items()
                },
            },
            indent=2,
        )
    )
    return 0 if all(v["pass"] for v in artifact["assertions"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
