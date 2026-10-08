#!/usr/bin/env python3
"""post-agent-record.py — canonical R1/R2/R11 tool (consolidates 17 tmp post_* scripts)
Writes an agent record via the nebula REST API.

Usage:
  post-agent-record.py --role engineer --title "Summary" --content "markdown"
  post-agent-record.py -r architect -t "Decision" -c "Details" --tags to:engineer,status:open
  post-agent-record.py -r DBA -t "Finding" -c "Body" --tags to:architect --tags type:report
  echo "body content" | post-agent-record.py -r engineer -t "Log entry"

Options:
  --role, -r         Role name (required: architect|engineer|planner|reviewer|analyst|inspector|critic)
  --title, -t        Record title (required)
  --content, -c      Record body in markdown (or read from stdin if not provided)
  --tags             Tags. REPEATABLE and/or comma-separated:
                     --tags a --tags b,c   or   --tags "a,b,c".
                     Repeated flags ACCUMULATE (they no longer truncate).
                     A single whole-string JSON array is also accepted.
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
    fallback = {"architect", "engineer", "engineer-ii", "engineer-iii",
                "devops", "topologist",
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
    for extra in ("engineer-ii", "engineer-iii", "analyst-ii", "dba"):
        roles[extra] = True
    return set(roles)


def parse_tags(values):
    """Parse one or more --tags occurrences into a clean tag list.

    Accepts REPEATED --tags occurrences (argparse action="append" feeds a
    list); each occurrence may be comma-separated, or a single whole-string
    JSON array (back-compat). Strips whitespace, drops empty segments,
    dedupes preserving first-seen order. Raises ValueError on JSON-ish
    fragments that are not a whole JSON array.
    """
    if not values:
        return []
    out, seen = [], set()
    for raw in values:
        s = (raw or "").strip()
        if not s:
            continue
        if s.startswith("["):
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError as exc:
                raise ValueError(f"--tags looks like JSON but does not parse: {exc}") from exc
            if not isinstance(parsed, list) or not all(isinstance(t, str) for t in parsed):
                raise ValueError("--tags JSON array must contain only strings")
            items = parsed
        else:
            if '"' in s:
                raise ValueError(
                    "--tags contains a quote but is not a JSON array — "
                    "use a JSON array string or comma-separated tags")
            items = s.split(",")
        for t in items:
            t = (t or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def parse_args():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--role", "-r", required=True)
    p.add_argument("--title", "-t", required=True)
    p.add_argument("--content", "-c", default=None)
    p.add_argument("--file", "-F", default=None,
                   help="Read record body from FILE (robust against shell quoting; preferred for long bodies)")
    p.add_argument("--force-content", action="store_true",
                   help="Allow --content that names an existing file path (deliberate path-as-body)")
    p.add_argument("--tags", action="append", default=None,
                   help="Tags. Repeatable and/or comma-separated "
                        "(--tags a --tags b,c or --tags 'a,b'); "
                        "a single whole-string JSON array is also accepted.")
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

    # Resolve content from --file, arg, or stdin (precedence: -F > -c > stdin).
    # Path-content guard (incident 2026-09-24): --content was handed a file
    # PATH (e.g. `-c /tmp/wp3_close.md`) and stored the path string as the
    # record body — 49 hollow records across the fleet, 5 unrecoverable. A
    # bare path is never a valid body: refuse when the string IS an existing
    # file, unless the caller passes --force-content (the explicit escape
    # hatch for legitimately posting a path-looking string).
    if args.file:
        if args.content:
            print("ERROR: --file and --content are mutually exclusive", file=sys.stderr)
            sys.exit(2)
        try:
            with open(args.file, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            print(f"ERROR: cannot read --file {args.file}: {exc}", file=sys.stderr)
            sys.exit(2)
    else:
        content = args.content
        if content is None:
            if not sys.stdin.isatty():
                content = sys.stdin.read()
            if not content:
                print("ERROR: --content is required (or pipe via stdin)", file=sys.stderr)
                sys.exit(2)
        elif not args.force_content and "\n" not in content and len(content) < 512 \
                and os.path.isfile(content):
            print(
                f"ERROR: --content looks like a path to an existing file ({content}). "
                f"Use --file {content} to post its body, or --force-content to store this string deliberately.",
                file=sys.stderr,
            )
            sys.exit(2)

    # Parse tags. Accepts REPEATED --tags occurrences (each may itself be
    # comma-separated) as well as a single whole-string JSON array.
    # Incident 2026-09-29 (DBA record 524564df): --tags was a plain option, so
    # repeated flags silently kept only the LAST value — 9/9 records filed
    # that day lost their to:* and type:* routing and vanished from
    # tag-routed inbox queries. action="append" plus parse_tags() makes
    # repeated flags accumulate. parse_tags() is now the primary validator;
    # the `bad` scan below remains as defense-in-depth.
    try:
        tag_list = parse_tags(args.tags)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
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
