#!/usr/bin/env python3
"""Record-durability checker: find hollow agent records.

A record is *hollow* when its content looks like a file path (the poster
was handed a path where a document was expected — the 09-23/09-24
incident, 49 records fleet-wide) or when it is suspiciously short
(< MIN_BYTES of content). Hollow records surface here the day they are
created instead of being discovered weeks later during an unrelated
audit.

Detection window is pointer-based: the caller (record-durability-wrap)
passes --since <ISO>; first run passes --baseline and only records the
high-water mark. Records already dispositioned carry
`hollow_content_audit` / `hollow_content_repair` metadata (I4-preserved
originals, pointer-type records) and are excluded. CI-ephemeral roles
(wr-conf-*) live in throwaway databases and are excluded by prefix.

Exit codes: 0 = clean (no findings), 1 = findings (the wrapper files an
inspection record), 2 = tool/environment error (nothing filed).
"""
from __future__ import annotations

import json
import os
import re
import sys

import psycopg2

DSN = os.environ.get("SRCDSN",
                     "postgresql://pguser:pgpass@localhost:5432/nexus")

MIN_BYTES = 50

# CI-ephemeral roles created by wr-conf E2E grant suites; their records
# live in throwaway databases — never expectation-relevant.
CI_EPHEMERAL_PREFIX = "wr-conf-"

# Metadata keys set by the hollow-record repair (2026-09-24) and by this
# sweep's reviewer: a record carrying any of these has been examined and
# its hollow shape is known/intentional.
AUDIT_META_KEYS = ("hollow_content_audit", "hollow_content_repair",
                   "record_durability_review")

# Content that is a single path-like token: absolute, home-relative,
# explicit-relative, or a bare filename with a document-ish extension.
_PATH_TOKEN = re.compile(
    r"^(?:/|~/|\./)[A-Za-z0-9_./-]+$"
    r"|^[A-Za-z0-9_.-]+\.(?:md|txt|log|json|sql|py|ts|js|yaml|yml|sh|toml|env)$"
)
# /dev/stdin and friends: the poster's stdin-consumption casualties.
_DEV_PATH = re.compile(r"^/dev/(?:stdin|stdout|stderr|null)$")


def hollow_reasons(content: str) -> list[str]:
    """Pure classification: why is this content hollow (may be empty)."""
    trimmed = (content or "").strip()
    reasons: list[str] = []
    if len(trimmed) < MIN_BYTES:
        reasons.append(f"content under {MIN_BYTES} bytes ({len(trimmed)})")
    if _PATH_TOKEN.match(trimmed) or _DEV_PATH.match(trimmed):
        reasons.append("content is a file path, not a document")
    return reasons


def is_exempt(role: str, metadata: dict | None) -> bool:
    if (role or "").startswith(CI_EPHEMERAL_PREFIX):
        return True
    meta = metadata or {}
    return any(key in meta for key in AUDIT_META_KEYS)


def scan(since: str) -> tuple[list[dict], str]:
    """Return (findings, max_created_at_iso) for records created after
    `since`. max_created_at is the new pointer (None when no rows)."""
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, role, record_type, title, content, metadata, created_at
            from nebula.agent_records
            where created_at > %(since)s
            order by created_at asc
            """,
            {"since": since},
        )
        rows = cur.fetchall()

    findings: list[dict] = []
    max_created: str | None = None
    for rid, role, rtype, title, content, metadata, created_at in rows:
        created_iso = created_at.isoformat()
        max_created = max(max_created, created_iso) if max_created else created_iso
        if is_exempt(role, metadata):
            continue
        reasons = hollow_reasons(content)
        if reasons:
            findings.append({
                "id": str(rid),
                "role": role,
                "record_type": rtype,
                "title": (title or "")[:120],
                "created_at": created_iso,
                "content_preview": (content or "")[:80],
                "reasons": reasons,
            })
    return findings, (max_created or since)


def main() -> int:
    args = sys.argv[1:]
    baseline = "--baseline" in args
    since = None
    if "--since" in args:
        since = args[args.index("--since") + 1]
    if not since:
        if not baseline:
            print("usage: check_record_durability.py (--baseline | --since <ISO>)",
                  file=sys.stderr)
            return 2
        since = "1970-01-01T00:00:00+00:00"  # baseline scans everything once

    try:
        findings, pointer = scan(since)
    except Exception as exc:  # noqa: BLE001 — tool error, wrapper decides
        print(f"tool error: {exc}", file=sys.stderr)
        return 2

    if baseline:
        print(json.dumps({"baseline_pointer": pointer,
                          "findings": findings}))
        return 0 if not findings else 1

    print(json.dumps({"since": since, "pointer": pointer,
                      "count": len(findings), "findings": findings},
                     indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
