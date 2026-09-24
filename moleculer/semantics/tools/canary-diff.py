#!/usr/bin/env python3
"""Byte-diff canary: moleculer/semantics twin (:4160) vs incumbent (:3160).

Read-only routes are fetched from BOTH services and compared after
normalizing genuinely volatile fields (health port/pid/timestamp; meta
active/total counts can drift between the two fetches — the STRUCTURE must
match). Envelope 404s and validation 400s are compared too (negative
parity). Write routes are NOT exercised here — the write-canary protocol
(marked synthetic rows via the A/B harness) governs those separately.
"""
import json
import sys
import urllib.request
import urllib.error

INCUMBENT = "http://localhost:3160"
TWIN = "http://localhost:4160"

READ_CASES = [
    # (label, path)
    ("health", "/health"),
    ("meta", "/api/meta"),
    # table CRUD list/get on a small vocabulary table
    ("relationship_type.list", "/api/relationship_type?limit=5"),
    ("evidence_type.list", "/api/evidence_type?limit=5"),
    ("snapshot.list", "/api/snapshot?limit=3"),
    ("drift_finding.list", "/api/drift_finding?limit=3"),
    # filters
    ("evidence_item.filter", "/api/evidence_item?limit=3"),
    ("statement_evidence.filter", "/api/statement_evidence?limit=3"),
    # 404 parity (missing rows)
    ("snapshot.get.miss", "/api/snapshot/00000000-0000-0000-0000-000000000000"),
    ("ca.envelope.miss", "/api/canonical_asset/00000000-0000-0000-0000-000000000000"),
    ("ar.envelope.miss", "/api/asset_revision/00000000-0000-0000-0000-000000000000"),
    # gateway 404 parity (unmatched route → Express HTML)
    ("unmatched.route", "/api/definitely_not_a_route"),
]

VOLATILE = {"pid", "timestamp", "port"}


def fetch(base, path):
    req = urllib.request.Request(base + path, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def normalize(obj):
    if isinstance(obj, dict):
        return {k: ("__VOLATILE__" if k in VOLATILE else normalize(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize(x) for x in obj]
    return obj


def canon(status, body, content_type):
    if "html" in (content_type or ""):
        return ("html", status, body)
    try:
        return ("json", status, json.dumps(normalize(json.loads(body)), sort_keys=True))
    except Exception:
        return ("raw", status, body)


def content_type_of(pair):
    # fetch() loses headers; re-derive from body shape in canon(). Unused.
    return None


def main():
    fails = []
    for label, path in READ_CASES:
        s1, b1 = fetch(INCUMBENT, path)
        s2, b2 = fetch(TWIN, path)
        c1 = canon(s1, b1, "html" if b1.lstrip().startswith("<!DOCTYPE") else "json")
        c2 = canon(s2, b2, "html" if b2.lstrip().startswith("<!DOCTYPE") else "json")
        if c1 == c2:
            print(f"OK    {label} [{c1[0]} {c1[1]}]")
        else:
            print(f"DIFF  {label}")
            print(f"      incumbent: {c1[0]} {c1[1]} {c1[2][:160]}")
            print(f"      twin     : {c2[0]} {c2[1]} {c2[2][:160]}")
            fails.append(label)
    print()
    if fails:
        print(f"FAIL: {len(fails)}/{len(READ_CASES)} cases differ: {fails}")
        return 1
    print(f"OK {len(READ_CASES)}/{len(READ_CASES)} canary cases byte-identical (normalized)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
