#!/usr/bin/env python3
"""check-forum-scope.py — prove forum-scoped Assembly triage reads real rows.

Why this exists (2026-11-12): `assembly.posts.forum_id` is NULL on every row
(15,461/15,461) because forums are identified by uuid and there is no numeric id
to populate that legacy bigint from. Any triage query that joined on it returned
ZERO ROWS SILENTLY — including two analyst sweeps whose job was to classify the
forum queue. The live key is `assembly.posts.forum_uuid`, and
`assembly.forum_scope_v` (assembly-views-procs.sql) exposes it for every post,
including orphans (forum_resolved=false) and expired threads (is_live=false)
that thread_list_v hides.

What this checks:
  C1 the view resolves rows at all — zero is a failure, not an empty queue;
  C2 resolved membership matches the posts that actually carry a live forum key
     (so the view cannot silently drop or invent posts);
  C3 orphans are counted and reported, never defaulted onto a forum;
  C4 the legacy forum_id join still returns 0 — documented baseline, printed so
     the next reader sees why the view exists.

Exit 0 when C1-C3 hold, 1 when any fails, 2 on a connection/usage problem.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

VIEW_TOTALS = """
SELECT count(*),
       count(*) FILTER (WHERE forum_resolved),
       count(*) FILTER (WHERE NOT forum_resolved),
       count(*) FILTER (WHERE is_live)
FROM assembly.forum_scope_v
""".strip()

RESOLVABLE_POSTS = """
SELECT count(*)
FROM assembly.posts p
WHERE p.forum_uuid IS NOT NULL
  AND EXISTS (SELECT 1 FROM assembly.forums f WHERE f.id = p.forum_uuid)
""".strip()

LEGACY_JOIN = """
SELECT count(*)
FROM assembly.posts p
JOIN assembly.forums f ON f.id::text = p.forum_id::text
""".strip()

BY_FORUM = """
SELECT forum_slug, count(*) AS threads,
       count(*) FILTER (WHERE is_live) AS live
FROM assembly.forum_scope_v
WHERE forum_slug IS NOT NULL
GROUP BY forum_slug
ORDER BY threads DESC
LIMIT 8
""".strip()


def psql(sql: str, dsn: str | None) -> list[list[str]]:
    """Run one query and return rows split on '|'. Raises RuntimeError.

    Connection defaults match the rest of the repo (host=localhost,
    user=pguser, db=nexus) so the checker works without a libpq socket; pass
    --dsn or the usual PG* env vars to point elsewhere.
    """
    cmd = ["psql", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", sql]
    if dsn:
        cmd += ["-d", dsn]
    else:
        cmd += [
            "-h", os.environ.get("PGHOST", "localhost"),
            "-U", os.environ.get("PGUSER", "pguser"),
            "-d", os.environ.get("PGDATABASE", "nexus"),
        ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError("psql not found on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("psql timed out after 30s")
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "").strip()[:400])
    return [line.split("|") for line in out.stdout.strip().splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", help="libpq connection string (default: PG env)")
    args = ap.parse_args(argv)

    try:
        totals = psql(VIEW_TOTALS, args.dsn)[0]
        total, resolved, orphans, live = (int(x) for x in totals)
        resolvable = int(psql(RESOLVABLE_POSTS, args.dsn)[0][0])
        legacy = int(psql(LEGACY_JOIN, args.dsn)[0][0])
        by_forum = psql(BY_FORUM, args.dsn)
    except (RuntimeError, IndexError, ValueError) as exc:
        print(f"forum-scope: ERROR {exc}", file=sys.stderr)
        return 2

    print(f"legacy_forum_id_join: {legacy} (expected 0 — the dead column)")
    print(f"view_total: {total}")
    print(f"view_resolved: {resolved}")
    print(f"view_orphans: {orphans}")
    print(f"view_live: {live}")
    print(f"posts_with_live_forum: {resolvable}")
    print("by_forum:")
    for row in by_forum:
        if len(row) >= 3:
            print(f"  {row[0]}: {row[1]} threads ({row[2]} live)")

    problems = []
    if total == 0:
        problems.append(
            "C1 forum_scope_v returned zero rows — forum-scoped triage would read "
            "an empty queue; check the view definition and posts.forum_uuid"
        )
    if resolved != resolvable:
        problems.append(
            f"C2 resolved membership {resolved} != posts carrying a live forum "
            f"key {resolvable} — the view is dropping or inventing rows"
        )
    if total != resolved + orphans:
        problems.append(
            f"C3 orphan accounting broken: total {total} != resolved {resolved} "
            f"+ orphans {orphans}"
        )

    if problems:
        for p in problems:
            print(f"forum-scope: FAIL {p}", file=sys.stderr)
        return 1
    print("verdict: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
