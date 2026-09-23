#!/usr/bin/env python3
"""Live canary diff — moleculer/draft (:4170) vs incumbent draft-srv (:3170).

Fires identical requests at both implementations and byte-compares
status line + body. The X-Nexus-Internal fleet secret is attached to every
request except the liveness probes (both implementations enforce Security
Pass Alpha; the secret value comes from NEXUS_INTERNAL_SECRET or
etc/fleet-internal.env on the host).

DB workbench is a LIVE data path: test-connection/databases/schemas/query all
open real connections with user-supplied credentials. The canary uses
READ-ONLY payloads against the local nexus DB (SELECTs, engine catalog,
bad-credential refusals) — no DDL, no writes, matching the C-cutover
"bounded evidence" posture. Two nodes can't share /api/health's timestamp or
latency fields (genuinely per-process values); those are shape-compared.

Usage:
    python3 tools/canary-diff.py [--port-a 3170] [--port-b 4170]
"""
import argparse
import http.client
import json
import os
import re
import sys

RANDOM_ENGINE = "___no_such_engine___"


def load_secret():
    s = os.environ.get("NEXUS_INTERNAL_SECRET")
    if s:
        return s
    for p in ("/home/codex/dev/nexus/etc/fleet-internal.env",):
        try:
            for line in open(p):
                if line.startswith("NEXUS_INTERNAL_SECRET="):
                    return line.strip().split("=", 1)[1]
        except OSError:
            pass
    return None


SECRET = load_secret()


def request(port, method, path, body=None, timeout=30, auth=True):
    conn = http.client.HTTPConnection("localhost", port, timeout=timeout)
    try:
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if auth and SECRET:
            headers["X-Nexus-Internal"] = SECRET
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            text = json.dumps(json.loads(raw), sort_keys=False, separators=(",", ":")) if raw else ""
        except Exception:
            text = raw.decode("utf-8", "replace")
        return resp.status, text
    finally:
        conn.close()


def normalize(text):
    """Mask genuinely per-process values: timestamps, latencies, versions."""
    t = text
    t = re.sub(r'"timestamp":"[^"]*"', '"timestamp":"<TS>"', t)
    t = re.sub(r'"latencyMs":\d+', '"latencyMs":<N>', t)
    t = re.sub(r'"executionTimeMs":\d+', '"executionTimeMs":<N>', t)
    t = re.sub(r'"rowCount":\d+', '"rowCount":<N>', t)
    t = re.sub(r'"version":"[^"]*"', '"version":"<V>"', t)
    # engines array in /api/health contains live availability, keep as-is
    return t


# (label, method, path, body, auth, normalize_result)
CASES = [
    # liveness — exempt from the secret on BOTH (proves the exemption)
    ("health-noauth", "GET", "/api/health", None, False, True),
    ("health-auth", "GET", "/api/health", None, True, True),

    # secret gate — no header on a data route => 403 {error:'forbidden'}
    ("engines-noauth-403", "GET", "/api/db/engines", None, False, False),
    # unknown route — unauthenticated => 403 too (gate precedes routing)
    ("unknown-noauth-403", "GET", "/api/db/bogus", None, False, False),

    # catalog + ladders
    ("engines", "GET", "/api/db/engines", None, True, False),
    ("test-conn-unknown-engine-400", "POST", "/api/db/test-connection",
     {"engine": RANDOM_ENGINE, "host": "localhost"}, True, False),
    ("databases-unknown-engine-400", "POST", "/api/db/databases",
     {"engine": RANDOM_ENGINE}, True, False),
    ("schemas-unknown-engine-400", "POST", "/api/db/schemas",
     {"engine": RANDOM_ENGINE}, True, False),
    ("query-missing-fields-400", "POST", "/api/db/query", {}, True, False),
    ("query-unknown-engine-400", "POST", "/api/db/query",
     {"connection": {"engine": RANDOM_ENGINE}, "sql": "SELECT 1"}, True, False),

    # mysql stub — provisioned but disabled => 501 (fail-visible)
    ("test-conn-mysql-501", "POST", "/api/db/test-connection",
     {"engine": "mysql", "host": "localhost"}, True, False),
    ("schemas-mysql-501", "POST", "/api/db/schemas", {"engine": "mysql"}, True, False),

    # bad credentials — both must refuse identically (502 envelope)
    ("databases-bad-creds-502", "POST", "/api/db/databases",
     {"engine": "postgres", "host": "localhost", "port": 5432,
      "database": "nexus", "username": "no-such-user", "password": "wrong"}, True, True),

    # live read-only query against the shared local DB (SELECT only)
    ("query-select-1", "POST", "/api/db/query",
     {"connection": {"engine": "postgres", "host": "localhost", "port": 5432,
                     "database": "nexus", "username": "pguser", "password": "pgpass"},
      "sql": "SELECT 1 AS one, 'x' AS x"}, True, True),
    ("query-bad-sql-error-body", "POST", "/api/db/query",
     {"connection": {"engine": "postgres", "host": "localhost", "port": 5432,
                     "database": "nexus", "username": "pguser", "password": "pgpass"},
      "sql": "SELECT * FROM ___no_such_table___"}, True, True),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port-a", type=int, default=3170)
    ap.add_argument("--port-b", type=int, default=4170)
    args = ap.parse_args()

    if not SECRET:
        print("FATAL: NEXUS_INTERNAL_SECRET not set and etc/fleet-internal.env not found", file=sys.stderr)
        sys.exit(2)

    same = diff = 0
    diffs = []

    for label, method, path, body, auth, do_norm in CASES:
        try:
            ra = request(args.port_a, method, path, body, auth=auth)
        except Exception as e:
            ra = ("ERR", f"{type(e).__name__}: {e}")
        try:
            rb = request(args.port_b, method, path, body, auth=auth)
        except Exception as e:
            rb = ("ERR", f"{type(e).__name__}: {e}")
        ta, tb = (normalize(ra[1]), normalize(rb[1])) if do_norm else (ra[1], rb[1])
        if ra[0] == rb[0] and ta == tb:
            same += 1
            print(f"  ok    {label}: {ra[0]}")
        else:
            diff += 1
            diffs.append(label)
            print(f"  DIFF  {label}: A={ra[0]} {ta[:160]!r}")
            print(f"        {' ' * len(label)}  B={rb[0]} {tb[:160]!r}")

    total = same + diff
    print(f"\n{same}/{total} byte-identical (normalized where noted)")
    if diffs:
        print("DIFFS: " + ", ".join(diffs))
    sys.exit(1 if diff else 0)


if __name__ == "__main__":
    main()
