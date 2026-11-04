#!/usr/bin/env python3
"""Inbox-pointer watchdog — report roles whose delivery watermark has gone stale
while mail is still waiting behind it.

Why this exists (2026-11-04): the tester role's pointer sat at
`2026-10-01T05:33:23.844Z` for a month while ~200 records tagged `to:tester`
accumulated behind it. Handoffs written to that directive were never read, and
nothing in the system said so — the queue looked stuck for reasons nobody could
name. The pointer was not corrupt: a role that never boots a session never
advances it, and three roles shared that exact timestamp because it was seeded
once by the 2026-10-01 Redis-restart correction (`db3992b2`) and never moved
again. An unread inbox that nobody reports is indistinguishable from an empty
one, so this script reports it.

What it will NOT do: advance a pointer. The watermark means "reviewed up to T",
not "delivered up to T". Advancing it to clear an alarm would hide precisely
the mail the alarm exists to surface (the same class of mistake as treating a
null pointer as an empty inbox — ruling 1fe2049de). Read-only by construction:
it issues GETs and nothing else.

A role is FLAGGED only when both hold:
  * the pointer is older than --max-age-days, and
  * at least one record tagged `to:<role>` was created after it (unread mail).

A stale pointer with an empty backlog is quiet on purpose: a dormant role with
no mail is not a problem, and flagging it would train everyone to ignore the
alarm.

Exit codes: 0 nothing flagged, 1 something flagged, 2 the lookup failed.

Usage:
  bin/inbox_pointer_watchdog.py                 # human table, exit 1 if flagged
  bin/inbox_pointer_watchdog.py --json          # machine-readable
  bin/inbox_pointer_watchdog.py --max-age-days 30
  bin/inbox_pointer_watchdog.py --role tester   # one mailbox only
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

DEFAULT_NEBULA_BASE = "http://localhost:3101"
DEFAULT_MAX_AGE_DAYS = 7


def _http_get_json(url: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local service)
        return json.loads(resp.read().decode("utf-8"))


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 watermark into an aware UTC datetime.

    `Z` is normalised because `datetime.fromisoformat` only learned to accept it
    in 3.11, and this script runs under whatever system python3 is present.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch_pointers(
    base: str = DEFAULT_NEBULA_BASE,
    http_get: Callable[[str], Any] = _http_get_json,
) -> Dict[str, Optional[str]]:
    """Role -> watermark (or None when the store has no pointer for the role)."""
    payload = http_get(f"{base.rstrip('/')}/api/inbox-pointers")
    pointers = payload.get("pointers", payload) if isinstance(payload, dict) else payload
    if not isinstance(pointers, dict):
        raise ValueError(f"unexpected /api/inbox-pointers shape: {type(pointers).__name__}")
    return {str(role): (ts or None) for role, ts in pointers.items()}


def _count(
    base: str,
    role: str,
    extra: Dict[str, str],
    http_get: Callable[[str], Any],
) -> int:
    """Exact count of records tagged `to:<role>` matching `extra`.

    `/api/agent-records` returns an exact `total`, so this is a count rather
    than a sample; `limit=1` keeps the payload small without changing it.
    """
    params: Dict[str, str] = {"tag": f"to:{role}", "limit": "1"}
    params.update(extra)
    url = f"{base.rstrip('/')}/api/agent-records?{urllib.parse.urlencode(params)}"
    payload = http_get(url)
    if isinstance(payload, dict) and isinstance(payload.get("total"), int):
        return payload["total"]
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    return len(items or [])


def _watermark_upper_bound(since: str) -> str:
    """Last instant covered by the watermark `since`, at millisecond granularity.

    `/api/agent-records` returns `createdAt` as epoch MILLISECONDS, so a
    watermark taken from a record is truncated to ms while `created_at` in
    Postgres carries microseconds. The record the role actually read therefore
    lives somewhere inside `since`'s millisecond — e.g. `...48.981523` behind a
    pointer of `...48.981` — so an exact-equality comparison never matches it.
    """
    dt = parse_iso(since)
    upper = dt.replace(microsecond=(dt.microsecond // 1000) * 1000 + 999)
    return upper.strftime("%Y-%m-%dT%H:%M:%S") + ".%03d999Z" % (upper.microsecond // 1000)


def count_backlog(
    base: str,
    role: str,
    since: Optional[str],
    http_get: Callable[[str], Any] = _http_get_json,
) -> int:
    """Records tagged `to:<role>` created after everything the watermark covers.

    The stored watermark is the `createdAt` of the newest record a role has
    already read, so that record is READ — but `/api/agent-records` implements
    `createdAfter` as `created_at >= $n` (inclusive, see routes.ts). Asking it
    directly for `createdAfter=<pointer>` therefore re-counts the newest
    already-read record as unread, forever: a mailbox that catches up and then
    goes dormant stays flagged, and re-advancing to the same timestamp cannot
    clear it. (Caught by tester review of PR #856: role `critic`, zero records
    newer than its pointer, verdict `backlog=1`.)

    Fixed by counting what is covered rather than by nudging the boundary:
    `createdBefore` is `created_at <= $n`, also inclusive, so subtracting the
    count at or before the watermark's last covered instant leaves exactly the
    unread set. That instant is the end of the watermark's millisecond, because
    the API reports epoch ms (see `_watermark_upper_bound`).

    The consequence, stated plainly: records written inside the SAME
    millisecond as the newest one the role read are treated as read. That is a
    watermark that is ms-granular by construction, not a guess — and the
    alternative (a permanent false alarm on a caught-up mailbox) is strictly
    worse. Anything at or after the next millisecond is counted.

    With no pointer the whole mailbox is unread by definition — that is the
    `pointerMissing` case, and it is a real loss rather than an empty inbox
    (ruling 1fe2049de).
    """
    if not since:
        return _count(base, role, {}, http_get)
    total = _count(base, role, {}, http_get)
    covered = _count(base, role, {"createdBefore": _watermark_upper_bound(since)}, http_get)
    return max(total - covered, 0)


def assess(
    pointers: Dict[str, Optional[str]],
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    backlog_fn: Optional[Callable[[str, Optional[str]], int]] = None,
    roles: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Classify every mailbox. Pure apart from `backlog_fn`; returns all roles.

    `flagged` is the actionable subset: stale watermark AND unread mail. The
    full list is returned (not just the flagged subset) so a report can show a
    healthy mailbox instead of silently omitting it.
    """
    findings: List[Dict[str, Any]] = []
    selected = sorted(pointers) if not roles else [r for r in roles if r in pointers]

    for role in selected:
        pointer = pointers[role]
        entry: Dict[str, Any] = {
            "role": role,
            "pointer": pointer,
            "pointerMissing": pointer is None,
            "ageDays": None,
            "backlog": None,
            "stale": True,
            "flagged": False,
        }

        if pointer is not None:
            # An unparseable watermark is treated as stale rather than skipped:
            # a value we cannot read is not evidence that anything was read.
            try:
                age = now - parse_iso(pointer)
                entry["ageDays"] = round(age.total_seconds() / 86400.0, 2)
                entry["stale"] = age.total_seconds() > max_age_days * 86400
            except ValueError:
                entry["stale"] = True
                entry["pointerUnparseable"] = True

        if backlog_fn is not None:
            try:
                entry["backlog"] = backlog_fn(role, pointer)
            except Exception as exc:  # a failed count must not invent zero
                entry["backlogError"] = str(exc)

        entry["flagged"] = bool(entry["stale"] and (entry["backlog"] or 0) > 0)
        findings.append(entry)

    return findings


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report roles whose inbox pointer is stale while mail waits behind it "
                    "(read-only; never advances a pointer).",
    )
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA_BASE,
                    help=f"nebula REST base (default {DEFAULT_NEBULA_BASE})")
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                    help=f"flag a watermark older than this (default {DEFAULT_MAX_AGE_DAYS})")
    ap.add_argument("--role", action="append", default=None,
                    help="limit to one role (repeatable)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    base = args.nebula_url.rstrip("/")
    try:
        pointers = fetch_pointers(base)
        findings = assess(
            pointers,
            now=datetime.now(timezone.utc),
            max_age_days=args.max_age_days,
            backlog_fn=lambda role, since: count_backlog(base, role, since),
            roles=args.role,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    flagged = [f for f in findings if f["flagged"]]

    if args.json:
        print(json.dumps({
            "maxAgeDays": args.max_age_days,
            "checked": len(findings),
            "flagged": len(flagged),
            "roles": findings,
        }, indent=2))
    else:
        print(f"# inbox pointers older than {args.max_age_days}d are stale; "
              f"flagged = stale AND unread mail")
        if not findings:
            print("(no mailboxes found)")
        for f in findings:
            if f["flagged"]:
                state = "FLAG"
            elif f["stale"]:
                state = "quiet"   # stale but nothing waiting
            else:
                state = "ok"
            age = "missing" if f["pointerMissing"] else (
                "?" if f["ageDays"] is None else f"{f['ageDays']:.1f}d")
            if f.get("backlogError"):
                backlog = "ERR"
            elif f["backlog"] is None:
                backlog = "-"
            else:
                backlog = str(f["backlog"])
            print(f"[{state:5}] {f['role']:<16} age={age:<8} unread={backlog:<5} "
                  f"pointer={f['pointer'] or '(null)'}")

    if flagged:
        print(f"\n{len(flagged)} mailbox(es) have unread mail behind a stale watermark: "
              f"{', '.join(f['role'] for f in flagged)}", file=sys.stderr)
        print("Read them (bin/check-inbox.sh --role <role>), then let the role's "
              "own session advance the pointer.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
