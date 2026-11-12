#!/usr/bin/env python3
"""Opt-in, candidate-scoped Track A evaluation; never emits or mutates.

This CLI is intentionally disconnected from the Stage-1 timer and Stage 3.
It reads one candidate in a PostgreSQL READ ONLY transaction and fails closed
unless the candidate is undecided, readiness-qualified, relationally mapped,
and the existing V152 bridge resolves system_mapped to JSON true.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

PSQL = [
    "docker", "exec", "-i", "pgvector_db", "psql", "-X", "-U", "pguser",
    "-d", "nexus", "-t", "-A", "-q", "-v", "ON_ERROR_STOP=1",
]
MIN_READINESS = 0.70
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def candidate_query(candidate_id: str) -> str:
    """Return static SQL with a UUID-validated identifier embedded as a literal."""
    if not _UUID.fullmatch(candidate_id):
        raise ValueError("candidate_id must be a UUID")
    return f"""BEGIN READ ONLY;
WITH candidate AS MATERIALIZED (
    SELECT id, title, compilation_readiness, status, asset_id, system_id
    FROM nebula.harvest_candidates
    WHERE id = '{candidate_id}'::uuid
    LIMIT 1
)
SELECT jsonb_build_object(
    'id', c.id::text,
    'title', c.title,
    'compilation_readiness', c.compilation_readiness,
    'status', c.status,
    'asset_id_present', c.asset_id IS NOT NULL,
    'system_id_present', c.system_id IS NOT NULL,
    'mapping_evidence', bridge.evidence
)::text
FROM candidate c
LEFT JOIN LATERAL (
    SELECT resolution.read_shrapnel_state_member(
        c.asset_id::text, 'system_mapped', now()
    ) AS evidence
    WHERE c.compilation_readiness >= 0.70
      AND c.status IS NULL
      AND c.asset_id IS NOT NULL
      AND c.system_id IS NOT NULL
) bridge ON true;
COMMIT;"""


def classify_candidate(row: dict[str, Any] | None, candidate_id: str) -> dict[str, Any]:
    """Apply the fail-closed Track A rules to one read-only query result."""
    if row is None:
        return {"candidate_id": candidate_id, "track": "A", "eligible": False,
                "reason": "candidate_not_found"}

    readiness = row.get("compilation_readiness")
    try:
        readiness_value = float(readiness)
    except (TypeError, ValueError):
        readiness_value = None
    status = str(row.get("status") or "pending").lower()

    if readiness_value is None or readiness_value < MIN_READINESS:
        reason = "readiness_below_threshold_or_missing"
    elif status != "pending":
        reason = "terminal_status"
    elif not row.get("asset_id_present"):
        reason = "asset_id_missing"
    elif not row.get("system_id_present"):
        reason = "system_id_missing"
    else:
        evidence = row.get("mapping_evidence")
        allowed = (
            isinstance(evidence, dict)
            and evidence.get("status") == "resolved"
            and evidence.get("value") is True
        )
        reason = "eligible" if allowed else "system_mapped_unresolved_or_false"

    return {
        "candidate_id": str(row.get("id") or candidate_id),
        "title": row.get("title"),
        "readiness": readiness_value,
        "track": "A",
        "eligible": reason == "eligible",
        "reason": reason,
    }


def evaluate(candidate_id: str, runner=subprocess.run) -> dict[str, Any]:
    if not _UUID.fullmatch(candidate_id):
        raise ValueError("candidate_id must be a UUID")
    command = PSQL + ["-c", candidate_query(candidate_id)]
    result = runner(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode != 0:
        detail = (result.stderr or "read-only candidate query failed").strip()
        raise RuntimeError(detail[:500])
    output = (result.stdout or "").strip()
    if not output:
        return classify_candidate(None, candidate_id)
    try:
        row = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("database returned invalid candidate JSON") from exc
    if not isinstance(row, dict):
        raise RuntimeError("database returned an unexpected candidate row")
    return classify_candidate(row, candidate_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate_id", help="UUID of exactly one candidate to inspect")
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.candidate_id)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"candidate Track A read failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
