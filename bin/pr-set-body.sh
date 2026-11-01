#!/usr/bin/env bash
# pr-set-body.sh — set a pull request's body and PROVE it was written.
#
# Why this exists: `gh pr edit --body-file` reports success on this host while
# silently not writing the body. Observed on PR #830 (2026-11-01): the command
# exited 0 having printed only a Projects-classic deprecation warning, the body
# was unchanged, and `gh pr view --json body` showed the old text. The only
# reason it was caught is that the body was re-read afterwards. A tool that
# claims a write it did not perform is worse than no tool: the PR description is
# part of the review surface, and a stale one misleads the reviewer.
#
# So this wrapper does exactly one thing differently — after the PATCH it reads
# the body back from the API and compares. No verification, no success.
#
# Usage:
#   pr-set-body.sh --repo OWNER/REPO --pr N --body-file path/to/body.md
#   pr-set-body.sh --repo OWNER/REPO --pr N --body "inline markdown"
#   cat body.md | pr-set-body.sh --repo OWNER/REPO --pr N
#
# Options:
#   --repo OWNER/REPO   Repository (default: derived from $GH_REPO if set)
#   --pr, -p N          Pull request number (required)
#   --body, -b TEXT     Body markdown (inline)
#   --body-file, -f F   Body markdown read from file
#   --no-verify         Skip the read-back (NOT recommended; escape hatch only)
#   -h, --help          Show this help
#
# Exit codes:
#   0  body written AND read back identical
#   1  body not written, or written but read-back differs (reports which)
#   2  usage error
#
# Testability: the gh binary is taken from ${GH_BIN:-gh} so the guard in
# bin/tests/test_pr_set_body.py can exercise the verification path against a
# fake that deliberately no-ops the PATCH.
set -euo pipefail

GH_BIN="${GH_BIN:-gh}"

die() { echo "pr-set-body: $*" >&2; exit 2; }

REPO="${GH_REPO:-}"
PR=""
BODY=""
BODY_FILE=""
VERIFY=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)      REPO="${2:-}"; shift 2 ;;
    --pr|-p)     PR="${2:-}"; shift 2 ;;
    --body|-b)   BODY="${2:-}"; shift 2 ;;
    --body-file|-f) BODY_FILE="${2:-}"; shift 2 ;;
    --no-verify) VERIFY=0; shift ;;
    -h|--help)   sed -n '2,32p' "$0"; exit 0 ;;
    *)           die "unknown argument: $1" ;;
  esac
done

[[ -n "$PR" ]] || die "--pr is required"
[[ -n "$REPO" ]] || die "--repo is required (or set GH_REPO)"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# Resolve the body to a file: the PATCH reads it via JSON so long bodies never
# hit argv limits, and the read-back comparison needs the exact bytes sent.
if [[ -n "$BODY_FILE" ]]; then
  [[ -f "$BODY_FILE" ]] || die "body file not found: $BODY_FILE"
  cp "$BODY_FILE" "$WORKDIR/new-body.md"
elif [[ -n "$BODY" ]]; then
  printf '%s' "$BODY" > "$WORKDIR/new-body.md"
else
  # No body given: read stdin, exactly like `gh pr edit -F -`. Redirected to a
  # file rather than captured with $(cat), because command substitution strips
  # trailing newlines and this tool must send the bytes it was handed.
  if [[ -t 0 ]]; then
    die "no body supplied (use --body, --body-file, or pipe to stdin)"
  fi
  cat > "$WORKDIR/new-body.md"
fi

command -v "$GH_BIN" >/dev/null 2>&1 || die "gh binary not found: $GH_BIN"

# Snapshot the old body so a no-op can be named as such rather than reported as
# a generic mismatch.
OLD_BODY="$("$GH_BIN" api "repos/$REPO/pulls/$PR" --jq .body 2>/dev/null || true)"

"$GH_BIN" api -X PATCH "repos/$REPO/pulls/$PR" \
  --input - >/dev/null <<EOF
$(python3 -c 'import json,sys; print(json.dumps({"body": open(sys.argv[1]).read()}))' "$WORKDIR/new-body.md")
EOF

if [[ "$VERIFY" -eq 0 ]]; then
  echo "pr-set-body: PATCH sent to $REPO#$PR (--no-verify: write NOT confirmed)"
  exit 0
fi

NEW_BODY="$("$GH_BIN" api "repos/$REPO/pulls/$PR" --jq .body 2>/dev/null || true)"

# Compare after normalising trailing newlines: GitHub strips a trailing newline,
# and that is not a failed write.
normalise() { printf '%s' "$1" | sed -e 's/\r$//' | sed -e :a -e '/^\n*$/{$d;N;};/\n$/ba'; }

if [[ "$(normalise "$NEW_BODY")" == "$(normalise "$(cat "$WORKDIR/new-body.md")")" ]]; then
  echo "pr-set-body: verified body on $REPO#$PR ($(wc -c < "$WORKDIR/new-body.md" | tr -d ' ') bytes)"
  exit 0
fi

if [[ "$(normalise "$NEW_BODY")" == "$(normalise "$OLD_BODY")" ]]; then
  echo "pr-set-body: FAILED — PATCH exited 0 but the body is unchanged." >&2
  echo "  This is the gh pr edit --body-file silent no-op. The body was NOT written." >&2
  exit 1
fi

echo "pr-set-body: FAILED — read-back does not match what was sent." >&2
echo "  sent $(wc -c < "$WORKDIR/new-body.md" | tr -d ' ') bytes, got ${#NEW_BODY} chars back" >&2
exit 1
