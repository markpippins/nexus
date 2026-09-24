#!/usr/bin/env python3
"""Read-only census for CD-2: ticket-expiry exposure and re-alert yield.

Answers, from live stores, exactly what the CD-2 design (see
docs/design-cd2-expiry-sweeper.md) would change:

  - how many tickets sit in expired / stale / open-past-expiry state
    per plan and role (the backlog the merged sweeper, PR #541,
    has already been transitioning);
  - which plans carry a REVIEW_REJECT in their receipt chain and with
    what scope (the re-alert routing input);
  - the re-alert yield: unique (plan, ticket-role, state) groups that
    would emit pages under the D-4 fingerprint contract — WITHOUT
    emitting anything or mutating any store.

Exit codes: 0 = census produced, 1 = exposure exists (informational —
the tool never mutates), 2 = tool error.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

DSN = os.environ.get("SRCDSN",
                     "postgresql://pguser:pgpass@localhost:5432/nexus")


def rows(sql: str) -> list[str]:
    ran = subprocess.run(["psql", DSN, "-Atc", sql],
                         capture_output=True, text=True, timeout=30)
    if ran.returncode != 0:
        raise RuntimeError(ran.stderr.strip()[:300])
    return [r for r in (ran.stdout or "").splitlines() if r]


def yield_groups(past_expiry: list[str]) -> set[str]:
    """Pure: unique (plan, role) groups from past-expiry rows — the
    D-4 pages the re-alert leg would emit right now. Row shape:
    plan|role|status|expires <ts>."""
    out = set()
    for r in past_expiry:
        parts = r.split("|")
        if len(parts) >= 2:
            out.add(f"{parts[0]}|{parts[1]}")
    return out


def main() -> int:
    try:
        state_counts = rows(
            "select status||'='||count(*) from vision.tickets group by status order by status;")
        past_expiry = rows(
            "select coalesce(plan_id,'?')||'|'||role||'|'||status||'|expires '"
            "||coalesce(to_char(expires_at,'MM-DD HH24:MI'),'-') from vision.tickets "
            "where status in ('open','claimed','stale') and expires_at is not null "
            "and expires_at < now() order by plan_id, role;")
        rejects = rows(
            "select distinct payload->>'plan_id' from resolution.receipt "
            "where kind='review_reject' order by 1;")
        scopes = rows(
            "select distinct on (payload->>'plan_id') payload->>'plan_id'||'|'||"
            "coalesce(payload->>'scope','(unset; default implementation)') "
            "from resolution.receipt where kind='review_reject' "
            "order by payload->>'plan_id', created_at desc;")
        sweep_events = rows(
            "select event_type::text||'='||count(*) from kernel.transition_event "
            "where event_type::text in ('transition.rejected','transition.requested') "
            "and aggregate_type='ticket' and payload::text like '%expiry%' "
            "group by event_type;")
    except RuntimeError as exc:
        print(f"tool error: {exc}", file=sys.stderr)
        return 2

    census = {
        "ticket_status_counts": state_counts,
        "past_expiry_open_rows": past_expiry,
        "plans_with_review_reject": rejects,
        "reject_scope_by_plan": scopes,
        "existing_expiry_transition_events": sweep_events,
        "re_alert_yield_groups": len(yield_groups(past_expiry)),
        "re_alert_yield_detail": sorted(yield_groups(past_expiry)),
    }
    print(json.dumps(census, indent=2))
    return 1 if past_expiry else 0


if __name__ == "__main__":
    sys.exit(main())
