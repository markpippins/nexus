#!/usr/bin/env bash
# supersede-record.sh — Decision 24 wrapper for retiring stale agent records.
#
# Implements the supersession convention ratified in architect Decision 24
# (record cfcc9d65) responding to engineer-ii proposal 42066ad6, plus
# Amendment 1's status:retired path. Tag vocabulary is registered in the
# `tag-routing-reference` procedure card (Amendment 2).
#
# Usage:
#   supersede-record.sh supersede --old <id> --new <id> [--reason "why"]
#   supersede-record.sh retire    --old <id> --note "what completed it"
#   supersede-record.sh supersede --old <id> --new <id> --dry-run
#
# Options:
#   --nebula-url  Nebula REST base (default: http://localhost:3101)
#   --role        Role recorded on the archive record (default: engineer)
#   --model       Model recorded on the archive record
#                 (default: $NEXUS_AGENT_MODEL or "opencode/big-pickle")
#   --dry-run     Read, compute, and print the plan; perform NO writes
#   -h, --help    Show this help
#
# supersede mode (content was replaced — a successor EXISTS):
#   1. GET the old record; refuse I4-exempt records (recordType prompt/response,
#      or any type:history tag) and records already status:superseded/retired.
#   2. Verify the successor record exists (the pointer must name a real id).
#   3. Create the verbatim archive record (type:archive) BEFORE any mutation,
#      header stating what/when/sha256, body byte-identical below a marker.
#   4. Round-trip verify: re-read the archive and byte-compare the verbatim
#      segment against the original content (sha256 equality).
#   5. Apply exactly ONE mutation to the old record: title prefixed
#      [SUPERSEDED -> <new8>], body replaced by a pointer (successor, archive
#      id, sha256, reason), tags + status:superseded + superseded-by:<new8>,
#      lifecycle statuses dropped (to:* breadcrumbs kept).
#   Sequence invariant (ratified): if steps 1-4 fail, step 5 never runs.
#
# retire mode (Amendment 1 — retirement WITHOUT a successor):
#   A status transition only: title prefixed [RETIRED <date>], closure note in
#   metadata, tags + status:retired with lifecycle statuses dropped. NO
#   archive, NO superseded-by pointer, NO placeholder successor.
#
# Exit codes: 0 success (or dry-run plan printed), 1 operational failure
#             (API error, round-trip mismatch — nothing further attempted),
#             2 usage error or policy refusal (I4-exempt record, already
#             superseded/retired, missing successor — nothing written)

set -euo pipefail

MODE="${1:-}"; shift || true
NEBULA_URL="http://localhost:3101"
ROLE="engineer"
MODEL="${NEXUS_AGENT_MODEL:-opencode/big-pickle}"
OLD_ID=""; NEW_ID=""; REASON=""; NOTE=""; DRY_RUN=0

usage() { sed -n '2,30p' "$0"; exit 2; }

[[ "$MODE" == "supersede" || "$MODE" == "retire" ]] || usage

while [[ $# -gt 0 ]]; do
  case "$1" in
    --old)        OLD_ID="$2"; shift 2 ;;
    --new)        NEW_ID="$2"; shift 2 ;;
    --reason)     REASON="$2"; shift 2 ;;
    --note)       NOTE="$2"; shift 2 ;;
    --nebula-url) NEBULA_URL="$2"; shift 2 ;;
    --role)       ROLE="$2"; shift 2 ;;
    --model)      MODEL="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    usage ;;
    *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$OLD_ID" ]] || { echo "ERROR: --old <record-id> is required" >&2; exit 2; }
if [[ "$MODE" == "supersede" && -z "$NEW_ID" ]]; then
  echo "ERROR: --new <successor-record-id> is required for supersede" >&2
  exit 2
fi
if [[ "$MODE" == "retire" && -z "$NOTE" ]]; then
  echo "ERROR: --note \"what completed this record\" is required for retire" >&2
  exit 2
fi

# Args travel via the environment, never interpolated into the Python source
# (same discipline as post-change-log.sh: a reason containing quotes or
# backslashes must not be able to corrupt the payload).
export SR_MODE="$MODE" SR_NEBULA_URL="$NEBULA_URL" SR_OLD_ID="$OLD_ID"
export SR_NEW_ID="$NEW_ID" SR_REASON="$REASON" SR_NOTE="$NOTE"
export SR_ROLE="$ROLE" SR_MODEL="$MODEL" SR_DRY_RUN="$DRY_RUN"

python3 << 'PYEOF'
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

MODE = os.environ["SR_MODE"]
BASE = os.environ["SR_NEBULA_URL"].rstrip("/")
OLD_ID, NEW_ID = os.environ["SR_OLD_ID"], os.environ["SR_NEW_ID"]
REASON, NOTE = os.environ["SR_REASON"], os.environ["SR_NOTE"]
ROLE, MODEL = os.environ["SR_ROLE"], os.environ["SR_MODEL"]
DRY_RUN = os.environ["SR_DRY_RUN"] == "1"

I4_RECORD_TYPES = {"prompt", "response"}

# Lifecycle tags are dropped on supersede/retire. This used to be an
# explicit allowlist of NON-TERMINAL statuses only ({status:open,
# status:claimed, status:in_progress}), which meant a record carrying a
# TERMINAL status kept it: retiring an attestation tagged status:done left
# the record simultaneously status:done AND status:retired, so a tag query
# still returned it as live. The contract in this file's header ("lifecycle
# statuses dropped") is only honoured if terminal statuses drop too, so
# every status:* tag drops and the operation's own status is authoritative.
# Non-status tags (to:* breadcrumbs, area:*, pr:*, type:*) are untouched.
LIFECYCLE_TAGS = {"status:open", "status:claimed", "status:in_progress"}


def is_lifecycle_tag(tag):
    """True for any lifecycle status tag, terminal or not."""
    return tag.startswith("status:")


def without_lifecycle(tags):
    return [t for t in tags if not is_lifecycle_tag(t)]


def fail(msg, code):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def call(url, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"_http_error": e.read().decode()[:300]}
    except Exception as e:
        return None, {"_http_error": str(e)}


def resolve(rid):
    """Full UUID -> itself; short id -> full id via the list endpoint.
    Nebula's point endpoint casts the id to uuid (a short id is a 400 there),
    but list items carry their real ids, so the short id is matched as an
    EXACT PREFIX on item ids across the newest 500 records. Title-substring
    matching would be ambiguous (a successor's title legitimately contains
    the old id it supersedes) and is deliberately not used."""
    if len(rid) == 36 and rid.count("-") == 4:
        return rid
    status, data = call(f"{BASE}/api/agent-records?limit=500")
    if status != 200:
        return None
    for item in data.get("items", []):
        if (item.get("id") or "").startswith(rid):
            return item["id"]
    return None


def fetch(rid):
    status, rec = call(f"{BASE}/api/agent-records/{rid}")
    if status != 200 or "id" not in rec:
        return None
    return rec


def sha(content):
    return hashlib.sha256(content.encode()).hexdigest()


def short(rid):
    return (rid or "")[:8]


now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
today = now_iso[:10]

# ── resolve short ids to full UUIDs before anything else ───────────
OLD_ID = resolve(OLD_ID)
if not OLD_ID:
    fail(f"could not resolve --old '{os.environ['SR_OLD_ID']}' to a record at "
         f"{BASE} (short ids resolve against the newest 500 records — pass "
         f"the full UUID for older records)", 1)
if NEW_ID:
    NEW_ID = resolve(NEW_ID)
    if not NEW_ID or len(NEW_ID) != 36:
        fail(f"could not resolve --new '{os.environ['SR_NEW_ID']}' to a full "
             f"record id at {BASE}", 1)

old = fetch(OLD_ID)
if old is None:
    fail(f"old record {OLD_ID} not found at {BASE} (reality-check the URL and id)", 1)

# ── policy gates (before ANY write) ────────────────────────────────────
if old.get("recordType") in I4_RECORD_TYPES:
    fail(f"record {short(OLD_ID)} is recordType {old.get('recordType')} — "
         f"prompt/response are append-only by definition (I4) and are exempt "
         f"from supersession/retirement (Decision 24)", 2)
if "type:history" in (old.get("tags") or []):
    fail(f"record {short(OLD_ID)} carries type:history — append-only (I4), "
         f"exempt from supersession/retirement", 2)
tags = list(old.get("tags") or [])
if "status:superseded" in tags:
    fail(f"record {short(OLD_ID)} is already status:superseded "
         f"(superseded-by: {[t for t in tags if t.startswith('superseded-by:')] or 'UNKNOWN'}); "
         f"refusing to double-supersede", 2)
if "status:retired" in tags:
    fail(f"record {short(OLD_ID)} is already status:retired; refusing", 2)

content = old.get("content") or ""
digest = sha(content)
old_title = old.get("title") or ""

if MODE == "supersede":
    new = fetch(NEW_ID)
    if new is None:
        fail(f"successor record {NEW_ID} not found at {BASE} — the pointer must "
             f"name a real record; create the successor first (append-only, "
             f"tagged supersedes:{short(OLD_ID)})", 1)
    if OLD_ID == NEW_ID:
        fail("--old and --new are the same record", 2)

    print(f"old:  {OLD_ID}  sha256={digest}  ({len(content)} chars)  "
          f"recordType={old.get('recordType')}")
    print(f"new:  {NEW_ID}  ({new.get('title') or ''})"[:150])

    archive_title = (f"ARCHIVE — verbatim original content of {short(OLD_ID)} "
                     f"(captured pre-supersession {today}, sha256 {digest[:12]})")
    archive_header = (
        f"# Archive snapshot (append-only)\n\n"
        f"Verbatim content of agent record `{OLD_ID}`, captured {now_iso} "
        f"immediately before its body was replaced with a supersession pointer "
        f"(Decision 24, architect record cfcc9d65). "
        f"`sha256(content) = {digest}`. Its content is superseded by record "
        f"`{NEW_ID}` (tagged `supersedes:{short(OLD_ID)}`). Content below the "
        f"marker is byte-identical to the original.\n\n---\n\n")
    archive_body = archive_header + content
    archive_payload = {
        "recordType": "report", "role": ROLE, "model": MODEL,
        "title": archive_title, "content": archive_body,
        "tags": ["type:archive", f"supersedes-relation:{short(OLD_ID)}",
                 f"superseded-by:{short(NEW_ID)}"],
    }
    pointer_body = (
        f"[SUPERSEDED — pointer record]\n\n"
        f"This record's content was replaced on {now_iso} and is preserved "
        f"verbatim (Decision 24 — archive-before-mutation, exactly one pointer "
        f"mutation).\n\n"
        f"- Successor: `{NEW_ID}` (tagged `supersedes:{short(OLD_ID)}`, "
        f"status:active)\n"
        f"- Verbatim archive: `<archive-id>` (sha256 of original content: "
        f"`{digest}`)\n"
        f"- Reason: {REASON or 'not stated'}\n\n"
        f"The original body ({len(content)} chars) is recoverable "
        f"byte-identically from the archive record named above. Do not edit "
        f"this pointer further; create a new successor instead.")

    if DRY_RUN:
        print(f"[dry-run] would POST archive record: {archive_title}")
        print(f"[dry-run] would PATCH old record -> title "
              f"'[SUPERSEDED → {short(NEW_ID)}] {old_title}'")
        print(f"[dry-run] would set tags: {without_lifecycle(tags) + ['status:superseded', f'superseded-by:{short(NEW_ID)}']}")
        print("[dry-run] no writes performed")
        sys.exit(0)

    # ── step 3: archive BEFORE mutation ────────────────────────────────
    status, created = call(f"{BASE}/api/agent-records", archive_payload, "POST")
    if status != 201 or "id" not in created:
        fail(f"archive creation failed (HTTP {status}: "
             f"{created.get('_http_error', '')[:200]}) — the old record was "
             f"NOT mutated (sequence invariant held)", 1)
    archive_id = created["id"]
    print(f"archive created: {archive_id}")

    # ── step 4: round-trip verify ──────────────────────────────────────
    back = fetch(archive_id)
    if back is None:
        fail(f"archive {archive_id} unreadable after creation — the old record "
             f"was NOT mutated; resolve the API issue and re-run (a second "
             f"archive will be created)", 1)
    stored = back.get("content") or ""
    marker = "\n---\n\n"
    verbatim = stored.split(marker, 1)[1] if marker in stored else None
    if verbatim is None or sha(verbatim) != digest:
        fail(f"archive round-trip MISMATCH for {archive_id} (verbatim segment "
             f"missing or sha256 differs) — the old record was NOT mutated; "
             f"investigate the archive record before retrying", 1)
    print(f"archive round-trip verified: sha256 {digest[:16]}… matches")

    # ── step 5: the ONE pointer mutation ───────────────────────────────
    new_tags = without_lifecycle(tags)
    new_tags += ["status:superseded", f"superseded-by:{short(NEW_ID)}"]
    patch = {
        "title": f"[SUPERSEDED → {short(NEW_ID)}] {old_title}",
        "content": pointer_body.replace("<archive-id>", archive_id),
        "tags": new_tags,
    }
    status, out = call(f"{BASE}/api/agent-records/{OLD_ID}", patch, "PATCH")
    if status != 200 or out.get("_http_error"):
        fail(f"pointer PATCH failed (HTTP {status}: "
             f"{out.get('_http_error', '')[:200]}) — archive {archive_id} "
             f"exists and is verified; the old record may still carry its "
             f"original content; re-run supersede to retry the pointer step",
             1)

    # ── verify the mutation landed ─────────────────────────────────────
    check = fetch(OLD_ID)
    ok = (check and check.get("title", "").startswith(f"[SUPERSEDED → {short(NEW_ID)}]")
          and "status:superseded" in (check.get("tags") or [])
          and f"superseded-by:{short(NEW_ID)}" in (check.get("tags") or []))
    if not ok:
        fail(f"pointer verification failed on read-back of {OLD_ID} — inspect "
             f"the record; archive {archive_id} remains valid", 1)
    print(f"OK superseded: {short(OLD_ID)} -> {short(NEW_ID)} "
          f"(archive {short(archive_id)}, sha256 {digest[:12]}…)")
    sys.exit(0)

# ── retire mode (Amendment 1): status transition only ─────────────────
if DRY_RUN:
    print(f"[dry-run] would PATCH old record -> title '[RETIRED {today}] {old_title}'")
    print(f"[dry-run] would set tags: {without_lifecycle(tags) + ['status:retired']}")
    print(f"[dry-run] would set metadata.retired = {{at, note}}; body UNCHANGED; no archive")
    print("[dry-run] no writes performed")
    sys.exit(0)

meta = dict(old.get("metadata") or {})
meta["retired"] = {"at": now_iso, "note": NOTE, "by": ROLE, "model": MODEL,
                   "convention": "Decision 24 (cfcc9d65) Amendment 1"}
new_tags = without_lifecycle(tags) + ["status:retired"]
patch = {"title": f"[RETIRED {today}] {old_title}", "tags": new_tags,
         "metadata": meta}
status, out = call(f"{BASE}/api/agent-records/{OLD_ID}", patch, "PATCH")
if status != 200 or out.get("_http_error"):
    fail(f"retire PATCH failed (HTTP {status}: {out.get('_http_error', '')[:200]})",
         1)
check = fetch(OLD_ID)
ok = (check and check.get("title", "").startswith(f"[RETIRED {today}]")
      and "status:retired" in (check.get("tags") or []))
if not ok:
    fail(f"retire verification failed on read-back of {OLD_ID}", 1)
print(f"OK retired: {short(OLD_ID)} (no archive, no successor — body unchanged)")
PYEOF