#!/usr/bin/env python3
"""post-agent-record.py — canonical R1/R2/R11 tool (consolidates 17 tmp post_* scripts)
Writes an agent record via the nebula REST API.

Usage:
  post-agent-record.py --role engineer --title "Summary" --content "markdown"
  post-agent-record.py -r architect -t "Decision" -c "Details" --tags to:engineer,status:open
  echo "body content" | post-agent-record.py -r engineer -t "Log entry"

Options:
  --role, -r         Role name (required: architect|engineer|planner|reviewer|analyst|inspector|critic)
  --title, -t        Record title (required)
  --content, -c      Record body in markdown (or read from stdin if not provided)
  --tags             Comma-separated tags (e.g. "to:architect,type:status-update")
  --record-type      Record type (default: engineering_log)
                     One of: report, analysis, assessment, inspection, prompt,
                             response, engineering_log, architecture_note, decision
  --level            Knowledge level (default: 3)
                     1=raw/operational, 2=structured, 3=planning/architectural, 4=meta
  --visibility       Visibility scope (default: architect)
  --model            AI model identifier for per-model attribution (optional)
  --nebula-url       Nebula API base URL (default: http://localhost:3101)
  -h, --help         Show this help

Exit codes: 0 ok, 1 API error, 2 usage error
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error


def _writable_roles():
    """Roles allowed to write agent records (canonical source:
    config/roles/roles.json nebulaCheck=true, plus registered active roles)."""
    registry = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "roles", "roles.json")
    fallback = {"architect", "engineer", "engineer-ii", "devops", "topologist",
                "planner", "reviewer", "analyst", "inspector", "critic"}
    try:
        with open(registry) as f:
            data = json.load(f)
        defaults = data.get("roleDefaults", {})
        roles = {}
        for name, overrides in (data.get("roles") or {}).items():
            if isinstance(overrides, dict) and overrides.get("nebulaCheck",
                                                             defaults.get("nebulaCheck", False)):
                roles[name] = True
    except Exception:
        return fallback
    # Registered active roles not (yet) in the registry are still writable.
    for extra in ("engineer-ii", "analyst-ii", "dba"):
        roles[extra] = True
    return set(roles)


def parse_args():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--role", "-r", required=True)
    p.add_argument("--title", "-t", required=True)
    p.add_argument("--content", "-c", default=None)
    p.add_argument("--tags", default="")
    p.add_argument("--record-type", default="engineering_log")
    p.add_argument("--level", type=int, default=3)
    p.add_argument("--visibility", default="architect")
    p.add_argument("--model", default=None)
    p.add_argument("--nebula-url", default="http://localhost:3101")
    p.add_argument("-h", "--help", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    if args.help:
        print(__doc__)
        sys.exit(0)

    # Validate role against the canonical role registry (config/roles/roles.json,
    # doc b80f0bdf). A role is writable when its effective nebulaCheck is true.
    # Roles registered in tackle.roles that predate the registry are kept.
    valid_roles = _writable_roles()
    if args.role not in valid_roles:
        print(f"ERROR: role must be one of: {', '.join(sorted(valid_roles))}", file=sys.stderr)
        sys.exit(2)

    # Resolve content from arg or stdin
    content = args.content
    if content is None:
        if not sys.stdin.isatty():
            content = sys.stdin.read()
        if not content:
            print("ERROR: --content is required (or pipe via stdin)", file=sys.stderr)
            sys.exit(2)

    # Parse tags. Accepts BOTH house forms: comma-separated
    # (--tags to:architect,type:status-update) and a JSON array string
    # (--tags '["to:architect","type:status-update"]'). Previously the JSON
    # form was silently comma-split into mangled elements (quotes and bracket
    # fragments stored in nebula.agent_records.tags), which broke tag routing
    # and inbox filters — 185 records affected, repaired 2026-09-22 with backup
    # in nebula.agent_records_tags_repair_20260922. This guard makes the JSON
    # form parse correctly and rejects any element that still carries JSON
    # punctuation instead of silently storing it.
    raw_tags = (args.tags or "").strip()
    tag_list: list[str] = []
    if raw_tags:
        if raw_tags.startswith("["):
            try:
                parsed = json.loads(raw_tags)
                if not isinstance(parsed, list) or not all(isinstance(t, str) for t in parsed):
                    raise ValueError("JSON tags must be an array of strings")
                tag_list = [t.strip() for t in parsed if t.strip()]
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"ERROR: --tags looks like JSON but does not parse as a string array: {exc}", file=sys.stderr)
                sys.exit(2)
        else:
            tag_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
    bad = [t for t in tag_list if '"' in t or t.startswith("[") or t.endswith("]")]
    if bad:
        print(f"ERROR: refusing to store malformed tags {bad} — use comma-separated or JSON array form", file=sys.stderr)
        sys.exit(2)

    payload = {
        "recordType": args.record_type,
        "role": args.role,
        "title": args.title,
        "content": content,
        "tags": tag_list,
        "level": args.level,
        "visibilityScope": args.visibility,
        "model": args.model,
    }

    url = f"{args.nebula_url}/api/agent-records"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            parsed = json.loads(body)
            rid = parsed.get("id") or parsed.get("recordId") or ""
            print(f"OK {resp.status} record_id={rid}")
    except urllib.error.HTTPError as e:
        print(f"ERROR HTTP {e.code}: {e.read().decode()[:400]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
