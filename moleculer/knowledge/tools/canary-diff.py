#!/usr/bin/env python3
"""Live canary diff — moleculer/knowledge (:4109) vs incumbent knowledge-srv (:3109).

Fires identical requests at both implementations and byte-compares
status line + body. Read paths only, plus the write paths' VALIDATION
envelopes (400s) and misses (404s) — a real POST/DELETE would mutate the
shared knowledge schema, which is a cutover prerequisite, not a canary act.

Usage:
    python3 tools/canary-diff.py [--port-a 3109] [--port-b 4109]
"""
import argparse
import http.client
import json

SAMPLE_SECTION, SAMPLE_ID = "authority_bindings", "deny_contract_promotion:binding:001"
MISSING_ID = "no-such-section/no-such-entity"


def request(port, method, path, body=None, timeout=15):
    conn = http.client.HTTPConnection("localhost", port, timeout=timeout)
    try:
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
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


def cases():
    return [
        # ── root + health ────────────────────────────────────────────
        ("root-index", "GET", "/", None),
        ("health", "GET", "/health", None),

        # ── entities (live rows) ─────────────────────────────────────
        ("entities-list", "GET", "/knowledge/entities?limit=2", None),
        ("entities-list-offset", "GET", "/knowledge/entities?limit=2&offset=3", None),
        ("entities-list-clamp-zero", "GET", "/knowledge/entities?limit=0", None),
        ("entities-list-clamp-huge", "GET", "/knowledge/entities?limit=99999", None),
        ("entities-list-nan", "GET", "/knowledge/entities?limit=abc", None),
        ("entities-filter-section", "GET", "/knowledge/entities?section=concepts&limit=3", None),
        ("entities-filter-nomatch", "GET", "/knowledge/entities?section=no-such-section", None),
        ("entities-search", "GET", "/knowledge/entities?search=resolution&limit=2", None),
        ("entities-get", "GET", f"/knowledge/entities/{SAMPLE_SECTION}/{SAMPLE_ID}", None),
        ("entities-get-404", "GET", f"/knowledge/entities/{MISSING_ID}", None),
        ("entities-relations", "GET", f"/knowledge/entities/{SAMPLE_SECTION}/{SAMPLE_ID}/relations", None),
        ("entities-create-400", "POST", "/knowledge/entities", {"entity_id": "no-section-given"}),
        ("entities-purge-400", "DELETE", "/knowledge/entities", None),

        # ── edges (live rows) ────────────────────────────────────────
        ("edges-list", "GET", "/knowledge/edges?limit=2", None),
        ("edges-list-filtered", "GET", "/knowledge/edges?source_section=concepts&limit=2", None),
        ("edges-list-nomatch", "GET", "/knowledge/edges?source_section=no-such-section", None),
        ("edges-create-400", "POST", "/knowledge/edges", {"source_id": "x"}),
        ("edges-delete-404", "DELETE", "/knowledge/edges/00000000-0000-0000-0000-00000000dead", None),

        # ── cross-references ─────────────────────────────────────────
        ("xrefs-list", "GET", "/knowledge/cross-references?limit=5", None),
        ("xrefs-create-400", "POST", "/knowledge/cross-references", {"map_name": "m"}),

        # ── migrations + summary ─────────────────────────────────────
        ("migrations-list", "GET", "/knowledge/migrations?limit=3", None),
        ("migrations-clamp", "GET", "/knowledge/migrations?limit=1000", None),
        ("summary", "GET", "/knowledge/summary", None),

        # ── unmatched route (Express finalhandler HTML) ──────────────
        ("unknown-route-404", "GET", "/knowledge/bogus", None),
        ("unknown-route-404-root", "GET", "/bogus", None),
        ("unknown-method", "PATCH", f"/knowledge/entities/{SAMPLE_SECTION}/{SAMPLE_ID}", {}),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port-a", type=int, default=3109)
    ap.add_argument("--port-b", type=int, default=4109)
    args = ap.parse_args()

    passed = failed = 0
    for name, method, path, body in cases():
        sa, ba = request(args.port_a, method, path, body)
        sb, bb = request(args.port_b, method, path, body)
        if sa == sb and ba == bb:
            passed += 1
            print(f"  ok  {name}")
        else:
            failed += 1
            print(f"DIFF {name}: A={sa} B={sb}")
            if sa != sb:
                print(f"   A body: {ba[:300]}")
                print(f"   B body: {bb[:300]}")
            else:
                for i, (x, y) in enumerate(zip(ba, bb)):
                    if x != y:
                        print(f"   first byte diff at {i}: A={ba[max(0,i-60):i+60]!r}")
                        print(f"                           B={bb[max(0,i-60):i+60]!r}")
                        break
                else:
                    print(f"   length differs: A={len(ba)} B={len(bb)}")
                    print(f"   A tail: {ba[-160:]!r}")
                    print(f"   B tail: {bb[-160:]!r}")

    print(f"\n{passed}/{passed + failed} byte-identical")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
