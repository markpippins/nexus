#!/usr/bin/env bash
# check-inbox.sh — R17 end-of-turn inbox check for any role.
#
# Queries the role's inbox through the canonical shared MCP client
# (python/nebula-mcp-client) against nebula-mcp on 3102 (Streamable HTTP).
# Uses the single-call `nebula_get_inbox` tool: it resolves the stored
# pointer, lists agent records tagged ["to:<role>"] created at/after it,
# and returns { role, pointer, items, count } in one round-trip.
#
# The inbox pointer (last-seen timestamp) lives at
#   http://localhost:3101/api/inbox-pointer/<role>
# and filters records created after it. Records' createdAt is epoch ms; the
# pointer/createdAfter expect ISO strings (only ISO was verified to parse).
#
# Usage:
#   check-inbox.sh [--role <role>] [--pointer <ISO>|--since <SPEC>|--all]
#                  [--limit N] [--update-pointer] [--raw]
#
# Options:
#   --role <role>       role whose inbox to query (default: engineer)
#   --pointer <ISO>     explicit createdAfter timestamp (overrides stored ptr)
#   --since <SPEC>      relative lookback, e.g. 7d / 12h / 30m / 45s — convenience
#                       sugar for --pointer "$(date ...)" (non-destructive)
#   --all               ignore the stored pointer; list most recent records
#   --limit N           max records to return (default: 50; server caps at 100)
#   --update-pointer    after listing, PUT the pointer to the newest record's
#                       createdAt (converted to ISO) so the next check is clean
#   --raw               print the raw MCP result JSON instead of summaries
#   -h, --help          show this help
#
# Env: NEBULA_MCP_BASE overrides the MCP endpoint (default
# http://localhost:3102) — used by tests/bin/checks.py to point at a mock.
#
# Exit: 0 = ok (even with zero new records), 1 = transport/tool error,
# 2 = usage error, 3 = pointer advance REFUSED because the returned window
# looks truncated (raising the default limit and re-checking first prevents
# below-fold records from being marked seen without ever being shown).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../python/nebula-mcp-client"

if [ ! -d "$LIB_DIR" ]; then
    echo "ERROR: canonical MCP client lib not found at $LIB_DIR" >&2
    echo "       (expected at nexus/python/nebula-mcp-client/)" >&2
    exit 1
fi

PYTHONPATH="$LIB_DIR${PYTHONPATH:+:$PYTHONPATH}" exec python3 - "$@" <<'PY'
import json, os, re, sys
from datetime import datetime, timedelta, timezone

from nebula_mcp_client import McpClient

BASE = os.environ.get("NEBULA_MCP_BASE", "http://localhost:3102")

USAGE = """Usage: check-inbox.sh [--role <role>] [--pointer <ISO>|--since <SPEC>|--all]
                  [--limit N] [--update-pointer] [--raw]
Options:
  --role <role>       role whose inbox to query (default: engineer)
  --pointer <ISO>     explicit createdAfter timestamp (overrides stored ptr)
  --since <SPEC>      relative lookback: 7d / 12h / 30m / 45s (non-destructive)
  --all               ignore the stored pointer; list most recent records
  --limit N           max records to return (default: 50; server caps at 100)
  --update-pointer    after listing, PUT the pointer to the newest record's
                      createdAt (converted to ISO) — REFUSED at exit 3 if the
                      returned window looks truncated
  --raw               print the raw MCP result JSON instead of summaries
  -h, --help          show this help"""

# --- arg parsing -----------------------------------------------------------
role = "engineer"
pointer = None
since = None
all_records = False
limit = 50
update_pointer = False
raw = False
i = 0
args = sys.argv[1:]
while i < len(args):
    a = args[i]
    if a in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)
    elif a == "--role":
        i += 1
        if i >= len(args):
            print("ERROR: --role requires a value", file=sys.stderr); sys.exit(2)
        role = args[i]
    elif a == "--pointer":
        i += 1
        if i >= len(args):
            print("ERROR: --pointer requires a value", file=sys.stderr); sys.exit(2)
        pointer = args[i]
    elif a == "--since":
        i += 1
        if i >= len(args):
            print("ERROR: --since requires a value (e.g. 7d, 12h, 30m, 45s)", file=sys.stderr); sys.exit(2)
        since = args[i]
    elif a == "--all":
        all_records = True
    elif a == "--limit":
        i += 1
        if i >= len(args):
            print("ERROR: --limit requires a value", file=sys.stderr); sys.exit(2)
        try:
            limit = int(args[i])
        except ValueError:
            print("ERROR: --limit must be an integer", file=sys.stderr); sys.exit(2)
        if limit < 1:
            print("ERROR: --limit must be >= 1", file=sys.stderr); sys.exit(2)
    elif a == "--update-pointer":
        update_pointer = True
    elif a == "--raw":
        raw = True
    else:
        print("ERROR: unknown argument: %s" % a, file=sys.stderr)
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    i += 1

# --- resolve --since into an explicit pointer (non-destructive) ------------
if since is not None:
    if pointer is not None or all_records:
        print("ERROR: --since cannot be combined with --pointer or --all", file=sys.stderr)
        sys.exit(2)
    m = re.fullmatch(r"(\d+)([dhms])", since.strip().lower())
    if not m:
        print("ERROR: --since expects <N>d|h|m|s (e.g. 7d, 12h, 30m, 45s); got %r" % since, file=sys.stderr)
        sys.exit(2)
    n = int(m.group(1))
    unit = m.group(2)
    delta = {
        "d": timedelta(days=n),
        "h": timedelta(hours=n),
        "m": timedelta(minutes=n),
        "s": timedelta(seconds=n),
    }[unit]
    pointer = (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")

# --- MCP call --------------------------------------------------------------
try:
    client = McpClient(BASE)
except Exception as e:
    print("ERROR: cannot connect to nebula-mcp on 3102: %s" % e, file=sys.stderr)
    sys.exit(1)

def _records_from(payload):
    """Normalize an MCP tool result into a list of records.

    The tools return text JSON that may be a bare array or a paginated
    wrapper { items, total, page, pageSize }. Return (records, extra) where
    extra holds the raw wrapper (for --raw / pointer reporting).
    """
    if isinstance(payload, dict) and payload.get("content"):
        text = "".join(
            c.get("text", "")
            for c in payload["content"]
            if isinstance(c, dict) and c.get("type") == "text"
        )
    else:
        text = payload if isinstance(payload, str) else json.dumps(payload)
    try:
        data = json.loads(text)
    except Exception:
        return None, text
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        items = data.get("items", [])
        return items, data
    return None, text

try:
    if pointer is None and not all_records:
        # Single-call path: nebula_get_inbox resolves the stored pointer itself.
        result = client.call("nebula_get_inbox", {"role": role, "limit": limit})
        records, extra = _records_from(result)
        if extra is None:
            extra = result if isinstance(result, dict) else {}
        if isinstance(extra, dict) and isinstance(extra.get("pointer"), str):
            pointer = extra["pointer"]
        header = "# inbox for %s since %s (limit %d)" % (role, pointer or "(none)", limit)
    else:
        # Explicit-pointer / --all path: nebula_list_agent_records with tag filter.
        # Match the default nebula_get_inbox path: filter by the routing tag
        # `to:<role>` ONLY — NOT by author `role` (records addressed to a
        # role come from OTHER roles). Passing both `role` + `tag` would
        # intersect to zero. Key must be `tag` (singular): `tags` (plural) is
        # silently stripped by the tool schema, dropping the filter (issue
        # #75).
        arguments = {"tag": ["to:" + role], "limit": limit}
        if pointer:
            arguments["createdAfter"] = pointer
        result = client.call("nebula_list_agent_records", arguments)
        records, extra = _records_from(result)
        header = ("# inbox for %s since %s (limit %d)" % (role, pointer, limit)
                  if pointer else "# inbox for %s — most recent %d records" % (role, limit))
except Exception as e:
    print("ERROR: %s" % e, file=sys.stderr)
    sys.exit(1)

if records is None:
    print(extra if isinstance(extra, str) else json.dumps(extra or result, indent=2), file=sys.stderr)
    sys.exit(1)

print(header)
if raw:
    print(json.dumps(records, indent=2))
else:
    if not records:
        print("(no new records)")
    for rec in records:
        ts = rec.get("createdAt", 0)
        try:
            iso = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        except Exception:
            iso = str(ts)
        print("- %s | %s | %s" % (iso, rec.get("recordType", "?"), rec.get("title", "")[:80]))

# --- truncation detection --------------------------------------------------
# The default (nebula_get_inbox) response only reports the RETURNED count, so
# "returned == limit" is a heuristic: the server may have hit its cap. The
# explicit-pointer/--all path (nebula_list_agent_records) returns a full
# `total`, so there the comparison is exact. A 10-record default fold once
# buried an attestation request below a routine sweep (record 192a829d);
# silence about truncation is what made that invisible.
def _truncation_note(records, extra, limit):
    """Return a truncation warning string, or None if the window looks complete."""
    if len(records) < limit:
        return None
    if isinstance(extra, dict) and isinstance(extra.get("total"), int):
        if extra["total"] > len(records):
            return ("WARN: inbox truncated: showing %d of %d matching records. "
                    "Re-run with a higher --limit before advancing the pointer."
                    % (len(records), extra["total"]))
        return None
    return ("WARN: inbox returned %d records == limit %d — there may be more "
            "below the fold. Re-run with a higher --limit before advancing "
            "the pointer." % (len(records), limit))

truncated = _truncation_note(records, extra, limit)
if truncated:
    print(truncated)

# --- optional pointer advance ----------------------------------------------
# NOTE: relies on records being newest-first, so max(createdAt) over the
# (possibly limited) window is the true newest — an unsorted list would only
# under-advance (duplicate delivery next turn), never lose records.
if update_pointer and records:
    if truncated:
        # Advancing now would mark below-fold records seen without ever
        # showing them — the exact failure mode that buried a request under
        # the old 10-record default. Withhold the write instead.
        print("REFUSING to update pointer: returned window looks truncated "
              "(see warning above). Re-run with a higher --limit, then "
              "advance with --update-pointer.", file=sys.stderr)
        sys.exit(3)
    newest = max((r.get("createdAt") or 0) for r in records)
    if newest:
        iso = datetime.fromtimestamp(newest / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            client.call("nebula_set_inbox_pointer", {"role": role, "timestamp": iso})
            print("# pointer updated to %s" % iso)
        except Exception as e:
            print("WARN: pointer update failed: %s" % e, file=sys.stderr)
PY
