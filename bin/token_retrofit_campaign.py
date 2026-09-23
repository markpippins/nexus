#!/usr/bin/env python3
"""token_retrofit_campaign.py — routing-token retrofit for the To Do forum.

Closes the unrouted-bucket problem found by lifecycle sweep #1 (366+ open
To Do threads with no [role] title token, invisible to the lifecycle
policy card, the boot-shim R16 scan, and the blackboard buckets).

Modes (zero writes unless --apply):
  --plan (default)  compute + print the retrofit plan from live data
  --apply           retitle via PUT /forums/threads/:id (supported,
                    authorship-preserving; used by sonar/jenkins sync) and
                    post a paired audit comment citing decision + evidence
  --explicit PATH   planner-provided {thread_id: role} overrides

Mapping kinds (Design §3):
  alias    token -> ratified role (dead/project tokens, case variants)
  compact  regex rewrite -> ratified role (style variants)
  close-or-keep  r0, zero-comment orphan tokens — needs planner go

I1/I2 invariants: retrofit NEVER advances ratings (retarget != advance),
never closes binding-domain outcomes, and every mutation pairs with an
audit comment. Authorship/role/model are preserved by the API.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"
DEFAULT_ASSEMBLY = "http://localhost:3107"
DEFAULT_NEBULA = "http://localhost:3101"
DEFAULT_STATE = str(Path.home() / ".cache" / "nexus-token-retrofit-state.json")

ROLE = "DBA"
MODEL = "freebuff/codebuff"
DECISION_THREAD = ""  # set via --decision-thread (planner thread id)

# ── mapping rules (Design §3; grounded in sweep #1 + live census) ────────

ALIASES: dict[str, str | None] = {
    # dead/project tokens -> ratified roles
    "peb": "engineer",
    "peb-kernel": "engineer",
    "promotion-batch": "engineer",
    "qbe": "engineer",
    "losm": "engineer",
    "losm-host": "engineer",
    "mesh": "engineer",
    "grid": "engineer",
    "property": "engineer",
    "calendar": "dba",
    "promotion": "planner",
    "promotion-flow": "planner",
    # case variants of ratified roles
    "dba": "dba",
    "planner": "planner",
    "analyst": "analyst",
    "engineer": "engineer",
    "architect": "architect",
    "operator": "operator",
    "devops": "devops",
    "inspector": "inspector",
    # known dead tokens: None -> close-or-keep (planner go required)
    "barbie-parity": None,
    "barbie": None,
}

COMPACT_RULES: list[tuple[re.Pattern, str]] = [
    # "[DBA] foo" style variants already normalize via case-fold + first segment
    (re.compile(r"^(dba|planner|analyst|engineer|architect|operator|devops|inspector)\b.*$"), r"\1"),
]

TOKEN_RE = re.compile(r"^\[([^\]→]*)\]")  # full match = the whole [token]


def normalize_token(title: str) -> str | None:
    """First-segment token (V192 rule), lowercased; hyphens preserved."""
    m = TOKEN_RE.match(title or "")
    if not m:
        return None
    tok = m.group(1).strip().lower()
    if not tok:
        return None
    return tok.split("/")[0].strip() or None


def classify_token(token: str | None, valid_roles: set[str]) -> str:
    if token is None:
        return "unrouted"
    if token in valid_roles:
        return "routed"
    if token in ALIASES:
        return "alias" if ALIASES[token] else "close-or-keep"
    return "unknown-token"


def plan_for(title: str, token: str | None, bucket: str,
             valid_roles: set[str]) -> dict | None:
    """Automatic plan (alias + compact only). Unrouted threads get NO
    automatic plan — they have no token to rewrite; they are grouped by
    author for the planner's default-author decision (Design §3)."""
    if bucket == "alias":
        role = ALIASES[token]
        new_title = f"[{role}] " + (TOKEN_RE.sub("", title or "", count=1).strip() or "(untitled)")
        return {"kind": "alias", "new_title": new_title}
    if bucket == "unknown-token":
        # compound tokens whose first segment is a ratified role
        seg = (token or "").split("-")[0]
        if seg in valid_roles:
            new_title = f"[{seg}] " + (TOKEN_RE.sub("", title or "", count=1).strip() or "(untitled)")
            return {"kind": "compact", "new_title": new_title}
        return None
    return None


# ── I/O (thin) ───────────────────────────────────────────────────────────

def http_json(url: str, payload=None, method=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body) if body else {}


def fetch_threads(dsn: str) -> tuple[list[dict], set[str]]:
    import psycopg2
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM nebula.roles")
        roles = {r[0].strip().lower() for r in cur.fetchall()}
        cur.execute(
            """
            SELECT p.id::text, p.title, COALESCE(p.rating, 0),
                   date_part('day', now() - p.created)::int,
                   (SELECT count(*) FROM assembly.comments c
                     WHERE c.post_id = p.id AND c.expiration_dt = 'infinity'),
                   p.role
            FROM assembly.posts p
            JOIN assembly.forums f ON f.id = p.forum_uuid
            WHERE f.slug = 'to-do' AND p.expiration_dt = 'infinity'
            ORDER BY p.created ASC
            """
        )
        rows = [{"id": i, "title": t, "rating": int(r), "age_days": int(a),
                 "n_comments": int(n), "author": au} for (i, t, r, a, n, au) in cur.fetchall()]
    return rows, roles


def live_audit_comments(dsn: str, post_id: str) -> int:
    """Count live retrofit audit comments on a thread (0 = not yet retrofitted)."""
    import psycopg2
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM assembly.comments
            WHERE post_id = %s
              AND text LIKE '%%Routing retrofit (%%automated, DBA)%%'
              AND expiration_dt = 'infinity'
            """,
            (post_id,),
        )
        return int(cur.fetchone()[0])


def poster_id(assembly_url: str) -> str:
    users = http_json(f"{assembly_url}/api/users")
    return next(u["id"] for u in users
                if (u.get("name") or "").lower() == ROLE.lower())


def retitle(assembly_url: str, tid: str, new_title: str) -> dict:
    return http_json(f"{assembly_url}/api/forums/threads/{tid}",
                     {"title": new_title[:500]}, method="PUT")


def audit_comment(assembly_url: str, pid: str, tid: str, kind: str,
                  decision_thread: str, old_title: str, new_title: str) -> str:
    body = (f"**Routing retrofit ({kind}; automated, DBA).** Title token "
            f"re-targeted for lifecycle routing: `{old_title}` → `{new_title}`. "
            f"Decision thread: {decision_thread or '(pending)'}; I1/I2 preserved — "
            f"status unchanged, reopen/re-challenge anytime. Retro-token campaign, "
            f"to-do-lifecycle-policy companion.")
    out = http_json(f"{assembly_url}/api/forums/threads/{tid}/comments",
                    {"body": body, "postedById": pid, "role": ROLE,
                     "model": MODEL})
    return out.get("id", "")


def post_record(nebula_url: str, title: str, content: str, tags: list[str],
                record_type: str = "report") -> str:
    d = http_json(f"{nebula_url}/api/agent-records", {
        "recordType": record_type, "role": ROLE, "title": title,
        "content": content, "tags": tags, "level": 1,
        "visibilityScope": "all"})
    return d.get("id") or d.get("record_id") or ""


# ── main ─────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--explicit", type=str, default=None,
                    help="JSON file {thread_id: role} planner overrides")
    ap.add_argument("--decision-thread", default="")
    ap.add_argument("--cap", type=int, default=50,
                    help="max retitles per run; 0 = no cap (apply everything planned)")
    ap.add_argument("--assembly-url", default=DEFAULT_ASSEMBLY)
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA)
    ap.add_argument("--dsn", default=os.environ.get("SRCDSN") or DEFAULT_DSN)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ")
    try:
        rows, roles = fetch_threads(args.dsn)
    except Exception as exc:
        print(f"HARD ERROR: cannot read threads: {exc}", file=sys.stderr)
        return 2

    buckets: dict[str, list[dict]] = {}
    for r in rows:
        tok = normalize_token(r["title"])
        r["token"], r["bucket"] = tok, classify_token(tok, roles)
        buckets.setdefault(r["bucket"], []).append(r)

    explicit = {}
    if args.explicit and Path(args.explicit).is_file():
        explicit = json.loads(Path(args.explicit).read_text())

    # Idempotency guard: threads already carrying a live retrofit audit comment
    # were routed by a previous pass — never re-plan them. (Regression: passes
    # 2..8 of the 2026-09-23 campaign re-processed the same oldest-50 threads
    # because the explicit-id check ignored current routed status, stacking 7
    # duplicate audit comments per thread; the duplicates were soft-expired
    # via expiration_dt and this guard added.)
    def retrofitted(tid: str) -> bool:
        try:
            return bool(live_audit_comments(args.dsn, tid))
        except Exception:
            # Fail-closed: an unreachable probe must not let a re-plan stack
            # duplicate audit comments again. Deferring a thread to the next
            # pass self-heals; duplicates do not.
            return True

    plan: list[dict] = []
    for r in rows:
        if retrofitted(r["id"]):
            continue
        p = plan_for(r["title"], r["token"], r["bucket"], roles)
        if r["id"] in explicit:
            role = explicit[r["id"]]
            new_title = f"[{role}] " + (TOKEN_RE.sub("", r["title"], count=1).strip() or "(untitled)")
            p = {"kind": "explicit", "new_title": new_title}
        if p:
            plan.append({**r, **p})

    print(f"threads={len(rows)} buckets: "
          + ", ".join(f"{k}={len(v)}" for k, v in sorted(buckets.items())))
    auto = [p for p in plan if p["kind"] in ("alias", "compact")]
    expl = [p for p in plan if p["kind"] == "explicit"]
    unrouted = buckets.get("unrouted", [])
    print(f"auto-plan (executable on go): {len(auto)}")
    for p in auto[:12]:
        print(f"  {p['kind']:8s} {p['id'][:8]} [{p.get('token')}] -> {(p.get('new_title') or '')[:66]}")
    if len(auto) > 12:
        print(f"  ... and {len(auto) - 12} more")
    by_author: dict[str, int] = {}
    for r in unrouted:
        by_author[r.get("author", "?")] = by_author.get(r.get("author", "?"), 0) + 1
    print(f"unrouted needing planner decision: {len(unrouted)} "
          f"(by author: {json.dumps(dict(sorted(by_author.items(), key=lambda kv: -kv[1])))})")
    print(f"explicit overrides loaded: {len(explicit)}")

    if not args.apply:
        print("plan mode: zero writes")
        return 0

    if not args.decision_thread:
        print("APPLY REFUSED: --decision-thread (planner go) is required",
              file=sys.stderr)
        return 2

    ev_id = post_record(args.nebula_url,
                        f"Token retrofit campaign {stamp} — evidence (census + plan)",
                        f"Census: " + json.dumps({k: len(v) for k, v in buckets.items()})
                        + f"\nPlan size: {len(plan)} (cap {args.cap})\nDecision thread: {args.decision_thread}",
                        ["to:dba", "dba", "token-retrofit", "type:report"])
    print(f"evidence record: {ev_id}")

    pid = poster_id(args.assembly_url)
    batch = plan if args.cap <= 0 else plan[: args.cap]
    applied, failed = [], []
    for p in batch:
        try:
            out = retitle(args.assembly_url, p["id"], p["new_title"])
            audit_comment(args.assembly_url, pid, p["id"], p["kind"],
                          args.decision_thread, p["title"], p["new_title"])
            applied.append((p["id"], out.get("title", "")))
        except Exception as exc:
            failed.append((p["id"], str(exc)[:120]))
    print(f"applied {len(applied)}/{len(plan)} (failed {len(failed)})")

    apply_id = post_record(args.nebula_url,
                           f"Token retrofit campaign {stamp} — applied ({len(applied)})",
                           f"Evidence {ev_id}; decision {args.decision_thread}; "                        f"applied {len(applied)}; failed {len(failed)}",
                        ["to:dba", "dba", "token-retrofit", "type:change"])
    Path(args.state).write_text(json.dumps(
        {"last_run": stamp, "evidence": ev_id, "apply": apply_id,
         "applied": [a for a, _ in applied]}, indent=2))
    print(f"apply record: {apply_id}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
