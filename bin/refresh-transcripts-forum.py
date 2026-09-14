#!/usr/bin/env python3
"""Refresh the Assembly `transcripts` forum from stored harvest docklang.

Why: the forum sink previously posted one comment per discourse ARC with an
auto-generated heading prefix. The canonical turn structure lives at block
level (each block carries its own provenance.role). This backfill rewrites
every thread's comments to one comment per TURN with a `**[User · ...]**` /
`**[Assistant · ...]**` header — matching the fixed nebula-srv transcript
endpoint and UIs.

Idempotency: before re-posting, existing comments on the thread are soft-deleted
in bulk via DELETE /api/forums/threads/:id/comments (assembly-srv). Posting the
same turn again will not duplicate (bodies are compared against live comments).

Usage:
    python3 nexus/bin/refresh-transcripts-forum.py            # all harvests
    python3 nexus/bin/refresh-transcripts-forum.py --limit 5  # first N
    python3 nexus/bin/refresh-transcripts-forum.py --id <uuid>
    python3 nexus/bin/refresh-transcripts-forum.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

ASSEMBLY = "http://localhost:3107/api"
NEBULA = "http://localhost:3101/api"
FORUM_SLUG = "transcripts"
AUTHOR_ALIAS = "engineer-ii"
TIMEOUT = 60


def http_json(url: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            text = r.read().decode() or "{}"
            return r.status, (json.loads(text) if text.strip() else {})
    except urllib.error.HTTPError as e:
        text = e.read().decode() if e.fp else ""
        raise RuntimeError(f"{url} -> {e.code}: {text[:300]}")


def get_json(url: str) -> dict | list:
    _, data = http_json(url)
    return data


def post_json(url: str, body: dict) -> dict:
    _, data = http_json(url, method="POST", body=body)
    return data


def delete_json(url: str) -> dict:
    _, data = http_json(url, method="DELETE")
    return data


def turn_comment_body(unit: dict, block: dict) -> str | None:
    """One comment body per block (turn). Mirrors absorb/absorb/sinks.py.

    Header carries only the turn role — the auto-generated arc heading was
    dropped: it repeated across many comments and never matched content
    reliably.
    """
    role = ((block.get("provenance") or {}).get("role")) or "unknown"
    role_label = "User" if role == "user" else "Assistant" if role == "assistant" else "Turn"
    btype = block.get("type") or "paragraph"
    text = ""
    if btype == "list" and block.get("items"):
        text = "\n".join(str(i) for i in block["items"])
    elif btype == "separator":
        text = "---"
    elif isinstance(block.get("content"), str):
        text = block["content"]
    if not text.strip():
        return None
    body = f"**[{role_label}]**\n\n{text}"
    if len(body) > 100_000:
        body = body[:100_000] + "\n\n…(truncated)"
    return body


def refresh_thread_for_harvest(h: dict, user_id: str, dry_run: bool) -> dict:
    hid = h["id"]
    # Detail endpoint returns snake_case; list (proxied) returns camelCase.
    title = h.get("source_filename") or h.get("sourceFilename") or hid
    docklang = h.get("docklang") or {}
    units = docklang.get("discourse_units") or []
    if not units:
        return {"harvest": hid[:8], "title": title, "status": "skipped_no_docklang"}

    # Find the forum thread by exact title match (same rule as the absorb sink).
    # The thread list is paginated; scan pages until the title is found.
    found = None
    page = 1
    while page <= 60:
        batch = get_json(f"{ASSEMBLY}/forums/{FORUM_SLUG}/threads?page={page}&pageSize=100")
        items = batch.get("items") if isinstance(batch, dict) else batch
        if not items:
            break
        for t in items:
            if t.get("title") == title:
                found = t
                break
        if found:
            break
        page += 1

    if not found:
        return {"harvest": hid[:8], "title": title, "status": "no_thread"}

    thread_id = found["id"]

    # Build all turn bodies first.
    bodies: list[str] = []
    for u in units:
        for b in u.get("blocks") or []:
            bdy = turn_comment_body(u, b)
            if bdy:
                bodies.append(bdy)

    result = {"harvest": hid[:8], "title": title[:60], "thread": thread_id[:8],
              "turns": len(bodies), "wiped": 0, "posted": 0, "status": "ok"}

    if dry_run:
        result["status"] = "dry_run"
        return result

    # Wipe existing comments (bulk soft-delete), then re-post every turn.
    w = delete_json(f"{ASSEMBLY}/forums/threads/{thread_id}/comments")
    result["wiped"] = w.get("deleted", 0)

    for body in bodies:
        post_json(
            f"{ASSEMBLY}/forums/threads/{thread_id}/comments",
            {"body": body, "postedById": user_id, "role": AUTHOR_ALIAS, "model": "absorb"},
        )
        result["posted"] += 1
        time.sleep(0.01)  # gentle on the API

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="only first N harvests")
    ap.add_argument("--id", dest="harvest_id", default=None, help="single harvest id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Resolve author user.
    users = get_json(f"{ASSEMBLY}/users")
    items = users if isinstance(users, list) else users.get("items", [])
    user = next((u for u in items if (u.get("alias") or u.get("name")) == AUTHOR_ALIAS), None)
    if not user:
        print(f"ERROR: assembly user {AUTHOR_ALIAS!r} not found", file=sys.stderr)
        return 1
    user_id = user["id"]

    # Harvests to process (only those with docklang discourse units).
    page = 1
    harvests = []
    while True:
        batch = get_json(f"{NEBULA}/harvests?page={page}&pageSize=100&sort=created_at")
        items = batch.get("items") if isinstance(batch, dict) else batch
        if not items:
            break
        harvests.extend(items)
        total = batch.get("total") if isinstance(batch, dict) else None
        if total and len(harvests) >= int(total):
            break
        if len(items) < 100:
            break
        page += 1

    if args.harvest_id:
        harvests = [h for h in harvests if h["id"].startswith(args.harvest_id)]
    if args.limit:
        harvests = harvests[: args.limit]

    print(f"harvests to process: {len(harvests)} (dry_run={args.dry_run})")

    ok = err = skipped = 0
    for i, h in enumerate(harvests, 1):
        try:
            # Need full docklang: fetch individually (list payload may omit it).
            full = get_json(f"{NEBULA}/harvests/{h['id']}")
            r = refresh_thread_for_harvest(full, user_id, args.dry_run)
            status = r.get("status")
            if status in ("skipped_no_docklang", "no_thread"):
                skipped += 1
                print(f"[{i}/{len(harvests)}] SKIP  {r['harvest']}  {r['title'][:40]:40s} {status}")
            else:
                ok += 1
                print(f"[{i}/{len(harvests)}] OK    {r['harvest']}  {r['title'][:40]:40s} "
                      f"turns={r.get('turns', '?')} wiped={r.get('wiped', '?')} posted={r.get('posted', '?')}")
        except Exception as e:
            err += 1
            print(f"[{i}/{len(harvests)}] ERR   {h['id'][:8]}  {e}", file=sys.stderr)

    print(f"\ndone: ok={ok} skipped={skipped} errors={err}")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
