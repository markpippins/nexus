#!/usr/bin/env python3
"""todo_lifecycle_sweep.py — daily To Do forum lifecycle sweep (automated).

Implements the recurring sweep defined by the `to-do-lifecycle-policy`
procedure card (doctrine thread discussions `ac2d1382`, ratified 2026-09-21):

  1. CLASSIFY every open To Do thread into the card's computed-on-read
     buckets (STALE-UNACKED, STALE-ORPHANED, COMPLETE-UNMARKED candidate,
     awaiting-pickup, in-flight, terminal, unrouted). Staleness is derived,
     never stored.
  2. ACT, narrowly: auto-close only STALE-ORPHANED threads under strict
     gates (see GATES below). Every close cites the run's evidence record
     id and the reopen path, advancing the status in the same gesture
     (the card's MUST rule). Nothing binding-domain is ever closed.
     COMPLETE-UNMARKED threads are REPORT-ONLY: marking r4 requires the
     verifier duty (performed verification) — a deliberate agent act, not
     a timer's.
  3. RECORD the run: an evidence record (classification census + intended
     actions, filed BEFORE any close so closes can cite it) and an apply
     record (outcomes + per-bucket delta vs the previous run). The apply
     records, chained by `series:nexus-todo-lifecycle` tags and the
     previous-record pointer, are the adoption-drift time series.
     Baseline data point: manual sweep #1, DBA record 53c8b76f (2026-09-22).

Canonical scheduling: systemd user timer `nexus-todo-lifecycle.timer`
(daily 03:15 local, Persistent=true). Failure model: on any hard error the
run exits non-zero before any write; a missing daily record in the time
series is itself the alert. Unit files follow house convention and live in
~/.config/systemd/user/ (see change-log entry for the deployed units).

Usage:
  todo_lifecycle_sweep.py                # dry-run: classify + print, zero writes
  todo_lifecycle_sweep.py --apply        # evidence record -> gated closes -> apply record
  todo_lifecycle_sweep.py --dry-run      # explicit dry-run
  todo_lifecycle_sweep.py --apply --cap 3

Options:
  --cap N             max auto-closes per run (default 10)
  --assembly-url URL  Assembly API base (default http://localhost:3107)
  --nebula-url URL    nebula-srv REST base (default http://localhost:3101)
  --dsn DSN           Postgres DSN (default $SRCDSN or local nexus)
  --state PATH        state file (default ~/.cache/nexus-todo-lifecycle-state.json)
  --quiet             machine-quiet output (timer mode)

Exit codes: 0 ok; 1 partial action failures; 2 hard error (no writes made).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"
DEFAULT_ASSEMBLY = "http://localhost:3107"
DEFAULT_NEBULA = "http://localhost:3101"
DEFAULT_STATE = str(Path.home() / ".cache" / "nexus-todo-lifecycle-state.json")

ROLE = "DBA"
MODEL = "automated/nexus-todo-lifecycle-sweep"
SERIES_TAG = "series:nexus-todo-lifecycle"

# Policy thresholds (to-do-lifecycle-policy card, ratified Q1/Q3).
ORPHAN_MIN_AGE_DAYS = 30     # card: stale; sweep #1 precedent closed ~55d
UNACKED_14_DAYS = 14
UNACKED_30_DAYS = 30
CU_MIN_AGE_DAYS = 7          # COMPLETE-UNMARKED window
DEFAULT_CAP = 10

# Token extraction: bracket prefix up to ] or →; then lowercase and take
# the first '/'-compound segment ([engineer/design] routes per the V192
# first-segment rule). Hyphens are KEPT — hyphenated roles are ratified
# (design-synthesist, lead-engineer, analyst-ii; sweep-#1 SQL cut at '-',
# which misclassified any hyphenated role as an orphan token).
TOKEN_RE = re.compile(r"^\[([^\]→]+)")


# ── pure classification (unit-tested, no I/O) ────────────────────────────

def normalize_token(title: str) -> str | None:
    m = TOKEN_RE.match(title or "")
    if not m:
        return None
    token = m.group(1).strip().lower()
    if not token:
        return None
    return token.split("/")[0].strip() or None


def classify(f: dict, valid_roles: set[str]) -> str:
    """Bucket one thread's features per the policy card's decision tree.

    f keys: rating (int, 0 when absent), age_days (int), n_comments (int),
    addr_engaged (bool), has_completed (bool), has_superseded (bool),
    token (str|None). Bucket names match sweep #1's vocabulary.
    """
    token = f.get("token")
    if token is None:
        return "unrouted"
    if token not in valid_roles:
        return "STALE-ORPHANED"  # token dominates: retarget-or-close
    rating = int(f.get("rating") or 0)
    age = int(f.get("age_days") or 0)
    if f.get("has_superseded"):
        return "STALE-SUPERSEDED?"
    if f.get("has_completed") and rating < 4 and age > CU_MIN_AGE_DAYS:
        return "COMPLETE-UNMARKED?"
    if rating <= 1 and not f.get("addr_engaged"):
        if age > UNACKED_30_DAYS:
            return "STALE-UNACKED>30d"
        if age > UNACKED_14_DAYS:
            return "STALE-UNACKED 14-30d"
        return "awaiting-pickup"
    if rating <= 1:
        return "addr-engaged-unrated"
    if rating == 6:
        return "reopened"
    if rating in (2, 3, 8):
        return "in-flight"
    if rating in (4, 5, 7):
        return "terminal"
    return "other"


def eligible_for_auto_close(f: dict, bucket: str) -> tuple[bool, str]:
    """Strict gates for an unattended orphan close. All must hold."""
    if bucket != "STALE-ORPHANED":
        return False, f"bucket {bucket} is not auto-closable"
    if int(f.get("rating") or 0) != 0:
        return False, "rating != 0 (some pickup happened)"
    if int(f.get("age_days") or 0) <= ORPHAN_MIN_AGE_DAYS:
        return False, f"age <= {ORPHAN_MIN_AGE_DAYS}d"
    if int(f.get("n_comments") or 0) != 0:
        return False, "has comments (engagement exists)"
    if f.get("has_completed"):
        return False, "completion language present (needs manual review)"
    return True, "gates passed"


def select_auto_closes(rows: list[dict], valid_roles: set[str], cap: int) -> list[dict]:
    """Oldest-first gated selection, capped per run."""
    eligible = []
    for r in rows:
        bucket = r.setdefault("bucket", classify(r, valid_roles))
        ok, _ = eligible_for_auto_close(r, bucket)
        if ok:
            eligible.append(r)
    eligible.sort(key=lambda r: int(r.get("age_days") or 0), reverse=True)
    return eligible[:cap]


def census(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["bucket"]] = out.get(r["bucket"], 0) + 1
    return dict(sorted(out.items()))


def render_delta(prev: dict[str, int] | None, cur: dict[str, int]) -> str:
    if not prev:
        return "baseline run (no previous census in state)"
    keys = sorted(set(prev) | set(cur))
    parts = []
    for k in keys:
        d = cur.get(k, 0) - prev.get(k, 0)
        parts.append(f"{k} {prev.get(k, 0)}→{cur.get(k, 0)}" + (f" ({'+' if d > 0 else ''}{d})" if d else ""))
    return "; ".join(parts)


def close_body(ev_id: str, f: dict) -> str:
    token = f.get("raw_token") or f.get("token") or "?"
    return (
        f"**STALE-ORPHANED close (automated lifecycle sweep {RUN_STAMP}, series {SERIES_TAG}, "
        f"evidence record {ev_id}).**\n\nTitle-routed token [{token}] is not in the ratified role "
        f"vocabulary (checked live against nebula.roles); rating 0, {f.get('age_days')}d old, "
        f"zero comments. Closed per the to-do-lifecycle-policy card (STALE-ORPHANED: retarget or "
        f"close). Nothing was executed — this close is "
        f"lifecycle bookkeeping only (I1/I2 preserved). **Retarget to a ratified role to reopen.** "
        f"Automated action; challenge by reopening."
    )


RUN_STAMP = ""  # set in main(); used by close_body for provenance


# ── I/O: DB + Assembly + nebula (thin) ───────────────────────────────────

def fetch_features(dsn: str) -> tuple[list[dict], set[str]]:
    """Raw per-thread features + the ratified role vocabulary (nebula.roles)."""
    import psycopg2  # local import: pure-function tests stay hermetic

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM nebula.roles")
        roles = {r[0].strip().lower() for r in cur.fetchall()}
        cur.execute(
            """
            SELECT p.id::text, p.title,
                   COALESCE(p.rating, 0),
                   date_part('day', now() - p.created)::int AS age_days,
                   (SELECT count(*) FROM assembly.comments c
                     WHERE c.post_id = p.id AND c.expiration_dt = 'infinity') AS n_comments,
                   EXISTS (SELECT 1 FROM assembly.comments c
                            WHERE c.post_id = p.id AND c.expiration_dt = 'infinity'
                              AND c.role = btrim((regexp_match(p.title, '^\\[([^\\]→]+)'))[1])) AS addr_engaged,
                   EXISTS (SELECT 1 FROM assembly.comments c
                            WHERE c.post_id = p.id AND c.expiration_dt = 'infinity'
                              AND c.text ~* '(^|[^A-Za-z])(completed|done|landed|merged|deployed|fixed)([^A-Za-z]|$)') AS has_completed,
                   EXISTS (SELECT 1 FROM assembly.comments c
                            WHERE c.post_id = p.id AND c.expiration_dt = 'infinity'
                              AND c.text ~* 'superseded by') AS has_superseded
            FROM assembly.posts p
            JOIN assembly.forums f ON f.id = p.forum_uuid AND f.slug = 'to-do'
            WHERE p.expiration_dt = 'infinity'
            ORDER BY p.created ASC
            """
        )
        rows = []
        for (tid, title, rating, age, ncom, addr, compl, sup) in cur.fetchall():
            token = normalize_token(title)
            rows.append({
                "id": tid, "title": title, "rating": int(rating), "age_days": int(age),
                "n_comments": int(ncom), "addr_engaged": bool(addr),
                "has_completed": bool(compl), "has_superseded": bool(sup),
                "token": token,
                "raw_token": (TOKEN_RE.match(title or "").group(1).strip()
                              if TOKEN_RE.match(title or "") else None),
            })
    return rows, roles


def http_json(url: str, payload: dict | None = None, method: str | None = None, timeout: int = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else {}


def post_record(nebula_url: str, title: str, content: str, tags: list[str], record_type: str = "report") -> str:
    d = http_json(f"{nebula_url}/api/agent-records", {
        "recordType": record_type, "role": ROLE, "title": title,
        "content": content, "tags": tags, "level": 1,
        "visibilityScope": "all",
    })
    return d.get("id") or d.get("record_id") or ""


def thread_comments(assembly_url: str, tid: str) -> list[dict]:
    d = http_json(f"{assembly_url}/api/forums/threads/{tid}")
    return d.get("comments", [])


def close_thread(assembly_url: str, tid: str, body: str) -> str:
    # Resolve the DBA poster id per run (never hardcode).
    users = http_json(f"{assembly_url}/api/users")
    poster = next(u["id"] for u in users if (u.get("name") or "").lower() == ROLE.lower())
    out = http_json(f"{assembly_url}/api/forums/threads/{tid}/comments", {
        "body": body, "postedById": poster, "role": ROLE, "model": MODEL,
        "statusRating": 7,
    })
    return out.get("id", "")


def already_closed(assembly_url: str, tid: str, ev_id: str) -> bool:
    try:
        return any(ev_id in (c.get("body") or "") for c in thread_comments(assembly_url, tid))
    except Exception:
        return False  # fail open: the close path is idempotent anyway (rating overwrite)


# ── main ─────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    global RUN_STAMP
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="perform gated closes + file records")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    ap.add_argument("--assembly-url", default=DEFAULT_ASSEMBLY)
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA)
    ap.add_argument("--dsn", default=os.environ.get("SRCDSN") or DEFAULT_DSN)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    apply_mode = args.apply and not args.dry_run
    RUN_STAMP = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ")

    try:
        rows, roles = fetch_features(args.dsn)
    except Exception as exc:
        print(f"HARD ERROR: classification failed, nothing written: {exc}", file=sys.stderr)
        return 2

    for r in rows:
        r["bucket"] = classify(r, roles)
    cur_census = census(rows)
    closable = select_auto_closes(rows, roles, args.cap)

    state_path = Path(args.state)
    prev = {}
    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text())
        except Exception:
            prev = {}
    prev_census = prev.get("census") or None
    delta = render_delta(prev_census, cur_census)

    lines = [f"To Do lifecycle sweep {RUN_STAMP} (apply={apply_mode})",
             f"open threads: {len(rows)}; valid roles: {len(roles)}"]
    lines += [f"  {k}: {v}" for k, v in cur_census.items()]
    lines.append(f"auto-close candidates (gated, cap {args.cap}): {len(closable)}")
    for r in closable:
        lines.append(f"  would close {r['id'][:8]} [{r.get('raw_token')}] {r['age_days']}d — {r['title'][:60]}")
    cu = [r for r in rows if r["bucket"] == "COMPLETE-UNMARKED?"]
    if cu:
        lines.append(f"COMPLETE-UNMARKED candidates (report-only, need manual verification): {len(cu)}")
        for r in cu:
            lines.append(f"  {r['id'][:8]} r{r['rating']} {r['age_days']}d — {r['title'][:60]}")
    if not args.quiet:
        print("\n".join(lines))
    print(f"census delta: {delta}")

    if not apply_mode:
        print("dry-run: no records filed, no threads touched")
        return 0

    # 1) evidence record FIRST (closes cite it — card requirement).
    intended = ", ".join(r["id"] for r in closable) or "none"
    ev_body = (
        f"Automated lifecycle sweep evidence (run {RUN_STAMP}, series {SERIES_TAG}).\n\n"
        f"Census ({len(rows)} open threads): " +
        ", ".join(f"{k}={v}" for k, v in cur_census.items()) +
        f"\nAuto-close candidates passing gates (rating 0, >{ORPHAN_MIN_AGE_DAYS}d, zero comments, "
        f"orphan token, cap {args.cap}): {len(closable)}\nIntended closes: {intended}\n"
        f"COMPLETE-UNMARKED candidates (report-only — the verifier duty is a deliberate agent act): "
        f"{len(cu)}\nDelta vs previous run: {delta}\n"
        f"Previous record: {prev.get('last_apply_record', 'none (baseline; manual sweep #1 = 53c8b76f)')}"
    )
    try:
        ev_id = post_record(args.nebula_url,
                            f"Automated To Do lifecycle sweep {RUN_STAMP} — evidence (census + intended closes)",
                            ev_body, ["to:dba", "dba", "lifecycle-sweep", "automated", SERIES_TAG, "type:report"])
    except Exception as exc:
        print(f"HARD ERROR: could not file evidence record, no closes performed: {exc}", file=sys.stderr)
        return 2

    # 2) gated closes, each idempotency-checked, each citing the evidence record.
    closed, failed = [], []
    for r in closable:
        try:
            if already_closed(args.assembly_url, r["id"], ev_id):
                closed.append((r["id"], "skipped (already closed by this run)"))
                continue
            cid = close_thread(args.assembly_url, r["id"], close_body(ev_id, r))
            closed.append((r["id"], cid))
        except Exception as exc:
            failed.append((r["id"], str(exc)[:120]))

    # 3) apply record with outcomes + delta; advance the state file.
    applied = ", ".join(f"{tid[:8]}({note[:8]})" for tid, note in closed) or "none"
    failures = ", ".join(f"{tid[:8]}: {why}" for tid, why in failed) or "none"
    apply_body = (
        f"Automated lifecycle sweep applied (run {RUN_STAMP}, series {SERIES_TAG}, evidence {ev_id}).\n\n"
        f"Census: " + ", ".join(f"{k}={v}" for k, v in cur_census.items()) +
        f"\nDelta: {delta}\nCloses applied ({len(closed)}/{len(closable)}): {applied}\n"
        f"Failures: {failures}\n"
        f"COMPLETE-UNMARKED candidates requiring manual verification: "
        + (", ".join(r["id"] for r in cu) or "none") +
        f"\nTime series note: this record is a data point; per-bucket deltas across the series "
        f"measure routing adoption and lifecycle latency. Baseline: manual sweep #1 (53c8b76f)."
    )
    apply_id = ""
    try:
        apply_id = post_record(args.nebula_url,
                               f"Automated To Do lifecycle sweep {RUN_STAMP} — applied ({len(closed)} closes)",
                               apply_body, ["to:dba", "dba", "lifecycle-sweep", "automated", SERIES_TAG, "type:change"])
    except Exception as exc:
        print(f"WARN: apply record filing failed (closes already done): {exc}", file=sys.stderr)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "last_run": RUN_STAMP,
        "last_evidence_record": ev_id,
        "last_apply_record": apply_id or prev.get("last_apply_record", ""),
        "census": cur_census,
        "closed_ids": [t for t, _ in closed],
    }, indent=2))

    print(f"evidence record {ev_id}; apply record {apply_id or 'FAILED'}; "
          f"closes {len(closed)}/{len(closable)} (failed: {len(failed)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
