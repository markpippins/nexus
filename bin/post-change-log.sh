#!/usr/bin/env bash
# post-change-log.sh — canonical R14 tool (consolidates 17 tmp post_* scripts)
# Posts a change summary to an Assembly forum, then VERIFIES the write.
#
# Usage:
#   post-change-log.sh --title "Fix: X" --body "markdown summary"
#   post-change-log.sh --title "Deploy: Y" < body.md
#   post-change-log.sh -t "Quick fix" -b "Details here"
#   post-change-log.sh --forum issues-and-open-questions -t "Blocker" -b "..."
#
# Options:
#   --title, -t     Post title (required, max 500 chars — server truncates)
#   --body, -b      Post body in markdown (or read from stdin if not provided)
#   --forum         Target forum slug (default: change-log; any Assembly forum,
#                   e.g. issues-and-open-questions)
#   --role          Role name (default: engineer)
#   --model         Model ID (default: $NEXUS_AGENT_MODEL or "opencode/big-pickle")
#   --assembly-url  Assembly API base URL (default: http://localhost:3107)
#   -h, --help      Show this help
#
# Verification and self-repair
#   After posting, the thread is re-read from the DETAIL endpoint
#   (/api/forums/threads/:id) and the persisted body compared against what was
#   submitted. If that body is genuinely empty, the body is re-posted as the
#   first comment and the thread is re-verified; if the content is still
#   unreadable the tool exits non-zero instead of reporting a post nobody can
#   read.
#
#   Verification deliberately does NOT consult the forum LIST endpoint: it does
#   not project `body`, so an empty `body` there says nothing about the write.
#   Treating a projection's missing field as a dropped field is what produced
#   five redundant hand-patched comments before this check existed
#   (DBA record 2a51e900).
#
# Exit codes: 0 posted and verified (or posted with verification unavailable,
#             which warns but does not fail — a non-zero exit would invite a
#             retry that duplicates the thread), 1 API error / repair failed /
#             body unrecoverable, 2 usage error

set -euo pipefail

# ── defaults ──────────────────────────────────────────────────────
ROLE="engineer"
MODEL="${NEXUS_AGENT_MODEL:-opencode/big-pickle}"
ASSEMBLY_URL="http://localhost:3107"
FORUM="change-log"
TITLE=""
BODY=""

# ── parse args ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --title|-t) TITLE="$2"; shift 2 ;;
    --body|-b)  BODY="$2"; shift 2 ;;
    --forum)     FORUM="$2"; shift 2 ;;
    --role)     ROLE="$2"; shift 2 ;;
    --model)    MODEL="$2"; shift 2 ;;
    --assembly-url) ASSEMBLY_URL="$2"; shift 2 ;;
    -h|--help)
      sed -n '4,34p' "$0"
      exit 0
      ;;
    *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
  esac
done

# ── validate ──────────────────────────────────────────────────────
if [[ -z "$TITLE" ]]; then
  echo "ERROR: --title is required" >&2
  exit 2
fi

if [[ -z "$BODY" ]]; then
  if [[ ! -t 0 ]]; then
    BODY=$(cat)
  fi
  if [[ -z "$BODY" ]]; then
    echo "ERROR: --body is required (or pipe content via stdin)" >&2
    exit 2
  fi
fi

# ── resolve role UUID ─────────────────────────────────────────────
USERS_URL="${ASSEMBLY_URL}/api/users"
ENGINEER_UUID=$(CL_USERS_URL="$USERS_URL" CL_ROLE_NAME="$ROLE" python3 << 'PYEOF'
import json, os, urllib.request
try:
    us = json.load(urllib.request.urlopen(os.environ['CL_USERS_URL'], timeout=10))
    want = os.environ['CL_ROLE_NAME'].lower()
    print(next((u['id'] for u in us if u.get('name', '').lower() == want), ''))
except Exception:
    pass
PYEOF
)

if [[ -z "$ENGINEER_UUID" ]]; then
  echo "ERROR: could not resolve ${ROLE} user UUID from ${USERS_URL}" >&2
  exit 1
fi

# ── post, verify, repair if needed ────────────────────────────────
# Title and body travel via the environment, never interpolated into the
# Python source: a body containing """ or a backslash used to corrupt or
# crash the payload.
export CL_ASSEMBLY_URL="$ASSEMBLY_URL"
export CL_POST_URL="${ASSEMBLY_URL}/api/forums/${FORUM}/threads"
export CL_TITLE="$TITLE"
export CL_BODY="$BODY"
export CL_ROLE="$ROLE"
export CL_MODEL="$MODEL"
export CL_UID="$ENGINEER_UUID"

RESULT=$(python3 << 'PYEOF'
import json, os, sys, urllib.error, urllib.request


def call(url, payload=None, method=None, timeout=15):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw.strip() else {})


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


base = os.environ["CL_ASSEMBLY_URL"]
thread_url = f"{base}/api/forums/threads/"
title, body = os.environ["CL_TITLE"], os.environ["CL_BODY"]
role, model, uid = os.environ["CL_ROLE"], os.environ["CL_MODEL"], os.environ["CL_UID"]

# ── 1. create ──────────────────────────────────────────────────────
payload = {"title": title, "body": body, "postedById": uid, "role": role, "model": model}
try:
    status, created = call(os.environ["CL_POST_URL"], payload, "POST")
except urllib.error.HTTPError as e:
    print(f"ERROR HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    fail(f"post failed: {e}")

tid = created.get("id") or ""
if not tid:
    fail(f"create returned no thread id: {json.dumps(created)[:200]}")
print(f"OK {status} thread={tid}")

if len(title) > 500:
    print(f"WARN: title is {len(title)} chars; the server truncates at 500", file=sys.stderr)

# ── 2. verify against the DETAIL endpoint ──────────────────────────
def read_thread():
    """(body_len, largest_comment_len), or None if the detail read failed.

    Only the detail endpoint is consulted. The forum list endpoint does not
    project `body`, so it cannot distinguish a dropped field from a projected
    one — reading it here is how roles concluded healthy posts were broken.
    """
    try:
        _, d = call(f"{thread_url}{tid}")
    except Exception:
        return None
    th = d.get("thread") if isinstance(d.get("thread"), dict) else d
    if not isinstance(th, dict) or not th:
        return None
    comments = d.get("comments") if isinstance(d.get("comments"), list) else []
    biggest = max((len(c.get("body") or "") for c in comments), default=0)
    return len(th.get("body") or ""), biggest


state = read_thread()
if state is None:
    # The post succeeded; we simply cannot confirm it. Do not fail: a non-zero
    # exit invites a retry that would duplicate the thread.
    print("WARN: thread detail unreadable; body not verified", file=sys.stderr)
    sys.exit(0)

body_len, comment_len = state
if body_len > 0:
    if body_len < len(body):
        print(f"WARN: persisted body is {body_len} chars but {len(body)} were "
              f"submitted — open thread {tid} and confirm", file=sys.stderr)
    print(f"verified body={body_len} chars (detail endpoint)")
    sys.exit(0)

# ── 3. genuinely empty on the detail read -> repair once ───────────
print("EMPTY body on detail read; re-posting as first comment", file=sys.stderr)
try:
    call(f"{thread_url}{tid}/comments",
         {"body": body, "postedById": uid, "role": role, "model": model}, "POST")
except Exception as e:
    fail(f"repair comment failed: {e}")

state = read_thread()
if state is None:
    fail(f"thread {tid} unreadable after repair; the summary may not be readable")
body_len, comment_len = state
if body_len == 0 and comment_len == 0:
    fail(f"body unrecoverable: detail body and comments are both empty after the "
         f"repair comment (thread {tid}) — repost manually")
if body_len == 0:
    print(f"repaired: thread body still empty, content is readable as the first "
          f"comment ({comment_len} chars)")
else:
    print(f"repaired: body now {body_len} chars")
PYEOF
)

echo "$RESULT"
if echo "$RESULT" | grep -q "^ERROR"; then
  exit 1
fi
exit 0
