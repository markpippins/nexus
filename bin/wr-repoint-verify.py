#!/usr/bin/env python3
"""wr-repoint-verify — per-tranche verification battery for the W1-W4 writer
repoints (plan 8261650, stages 2-5; consumer map e3850398).

Verifies each engineer tranche against the canonical store in one command:

  structural battery (always):
    S1 legacy_id coverage          S2 known prefix set (no invented vocabulary)
    S3 crosswalk invariant         S4 mirror orphans
    S5 vocabulary + silent-ELSE sentinel (no 'NEW')
    S6 bitemporal shape            S7 legacy freeze (Stage-1 baselines)
    S8 audit-trigger presence (conditional)

  tranche landing checks (need --since <ISO>):
    W2 losm_store                  prefix default 'vision.work_requests:'
    W3 conduit db_adapter          prefix default 'nebula.work_requests_history:'
    W4 cascade admission_subscriber  same default as W3 (disambiguate via --created-by)
  optional --legacy-prefix overrides the expected prefix; --created-by narrows
  attribution (W3/W4 share the nebula prefix by design — flagged as a design
  question for the repoint PRs, see the posted checklist).

  W1 vision-srv cutover (no --since): vision-srv :8003 must be DOWN, losm-host
  :8006 must be UP. URLs overridable via NEXUS_VISION_SRV_URL / NEXUS_LOSM_HOST_URL.

Statuses: PASS / FAIL / SKIP (not applicable or surface absent, e.g. post-V187
archive renames) / WARN (drift signal, does not fail the run).
Exit: 0 all pass-or-skip, 1 any FAIL, 2 environmental (cannot reach PG).

Read-only against the database: every check is SELECT-only; the HTTP probes
are GET-only. Baseline constants are the verified Stage-1 post-apply state
(apply record 09ac979a; crosswalk pin: architect attestation 06:41Z, DBA
record f08fdf0c — mirror keys carry the canonical PK, NOT the legacy_id tail).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_DSN = os.environ.get(
    "NEXUS_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus"
)
VISION_SRV_URL = os.environ.get("NEXUS_VISION_SRV_URL", "http://localhost:8003")
LOSM_HOST_URL = os.environ.get("NEXUS_LOSM_HOST_URL", "http://localhost:8006")

# Stage-1 baseline (apply record 09ac979a; architect-attested 06:41Z).
BASELINE = {
    "vision.work_requests": 6,
    "nebula.work_requests_history": 1,
    "vision.work_requests_history": 0,
    "resolution.work_request": 7,
    "vision_mirrors": 6,
}

KNOWN_PREFIXES = {"vision.work_requests", "nebula.work_requests_history"}
KNOWN_STATUSES = {"COMPLETED", "DRAFT"}
SILENT_ELSE_SENTINEL = "NEW"  # V186-VOCAB-001: any row with this is a regressor

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"


class Check:
    def __init__(self, cid, name, fn):
        self.id, self.name, self.fn = cid, name, fn


def _open_ended(ts) -> bool:
    if ts is None:
        return False
    s = str(ts)
    return "infinity" in s or s >= "9999-01-01"


# ── structural battery ────────────────────────────────────────────────────────

def s1_legacy_coverage(q, ctx):
    n = q("SELECT count(*) FROM resolution.work_request WHERE legacy_id IS NULL OR legacy_id = ''")[0][0]
    return (PASS if n == 0 else FAIL, f"rows with missing legacy_id: {n} (expected 0)")


def s2_prefix_set(q, ctx):
    rows = q("SELECT DISTINCT split_part(legacy_id, ':', 1) FROM resolution.work_request")
    seen = {r[0] for r in rows}
    unknown = seen - KNOWN_PREFIXES
    return ((PASS if not unknown else FAIL),
            f"prefixes: {sorted(seen)}" + (f" — UNKNOWN: {sorted(unknown)}" if unknown else ""))


def s3_crosswalk_invariant(q, ctx):
    """The architect's pin: mirror key tail == canonical PK, not legacy_id tail."""
    unlinked = q(
        "SELECT count(*) FROM resolution.work_request wr "
        "LEFT JOIN resolution.canonical_asset m "
        "  ON m.canonical_asset_id = 'asset:nexus:vision_work_requests:' || wr.id::text "
        "WHERE wr.legacy_id LIKE 'vision.work_requests:%' AND m.id IS NULL"
    )[0][0]
    return (PASS if unlinked == 0 else FAIL,
            f"vision rows without a linked mirror (keyed on canonical id): {unlinked}")


def s4_mirror_orphans(q, ctx):
    n = q(
        "SELECT count(*) FROM resolution.canonical_asset m "
        "WHERE m.canonical_asset_id LIKE 'asset:nexus:vision_work_requests:%' "
        "AND NOT EXISTS (SELECT 1 FROM resolution.work_request wr "
        "  WHERE 'asset:nexus:vision_work_requests:' || wr.id::text = m.canonical_asset_id)"
    )[0][0]
    return (PASS if n == 0 else FAIL, f"orphan mirrors: {n} (expected 0)")


def s5_vocabulary(q, ctx):
    rows = q("SELECT business_status, count(*) FROM resolution.work_request GROUP BY business_status")
    statuses = {r[0] for r in rows}
    unknown = statuses - KNOWN_STATUSES
    if SILENT_ELSE_SENTINEL in statuses:
        n_new = dict(rows).get(SILENT_ELSE_SENTINEL, 0)
        return FAIL, f"silent-ELSE sentinel: {n_new} row(s) with business_status='NEW' — a writer reintroduced the ELSE branch"
    return ((PASS if not unknown else FAIL),
            f"statuses: {sorted(statuses)}" + (f" — UNKNOWN: {sorted(unknown)}" if unknown else ""))


def s6_bitemporal(q, ctx):
    row = q(
        "SELECT count(*) FILTER (WHERE valid_from IS NULL), "
        "count(*) FILTER (WHERE valid_until IS NOT NULL AND NOT (" + _open_sql("valid_until") + ")), "
        "count(*) FILTER (WHERE recorded_on_dt IS NULL), "
        "count(*) FILTER (WHERE NOT (" + _open_sql("recorded_until_dt") + ")) "
        "FROM resolution.work_request"
    )[0]
    bad = sum(row)
    return (PASS if bad == 0 else FAIL,
            f"valid_from NULL: {row[0]}, valid_until closed: {row[1]}, "
            f"recorded_on NULL: {row[2]}, recorded_until closed: {row[3]} (all expected 0)")


def _open_sql(col: str) -> str:
    return f"({col}::text LIKE 'infinity%' OR {col} >= '9999-01-01'::timestamptz)"


def s7_legacy_freeze(q, ctx):
    out = []
    ok = True
    for table, expected in (("vision.work_requests", 6),
                            ("nebula.work_requests_history", 1),
                            ("vision.work_requests_history", 0)):
        try:
            n = q(f"SELECT count(*) FROM {table}")[0][0]
        except ctx["UndefinedTable"]:
            out.append(f"{table}=ABSENT(post-V187 SKIP)")
            continue
        good = n == expected
        ok = ok and good
        out.append(f"{table}={n}(baseline {expected})")
    canonical = q("SELECT count(*) FROM resolution.work_request")[0][0]
    out.append(f"canonical={canonical}")
    return ((PASS if ok else FAIL), "; ".join(out))


def s8_audit_presence(q, ctx):
    n = q(
        "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'resolution' AND c.relname = 'work_request' AND NOT t.tgisinternal"
    )[0][0]
    if n == 0:
        return SKIP, "no audit triggers on resolution.work_request yet — V156-pattern extension is a candidate follow-up"
    return (PASS, f"{n} audit trigger(s) present on the canonical surface")


STRUCTURAL = [
    Check("S1", "legacy_id coverage", s1_legacy_coverage),
    Check("S2", "prefix set", s2_prefix_set),
    Check("S3", "crosswalk invariant (mirror keyed on canonical id)", s3_crosswalk_invariant),
    Check("S4", "mirror orphans", s4_mirror_orphans),
    Check("S5", "vocabulary + silent-ELSE sentinel", s5_vocabulary),
    Check("S6", "bitemporal shape", s6_bitemporal),
    Check("S7", "legacy freeze (Stage-1 baselines)", s7_legacy_freeze),
    Check("S8", "audit-trigger presence", s8_audit_presence),
]


# ── tranche landing checks ────────────────────────────────────────────────────

def make_landing(cid, label, default_prefix):
    def check(q, ctx):
        since = ctx.get("since")
        prefix = ctx.get("legacy_prefix") or default_prefix
        if not since:
            return SKIP, "--since not given; run this right after the repoint's test write"
        cond = f"legacy_id LIKE '{prefix}%'"
        if ctx.get("created_by"):
            cond += f" AND created_by = '{ctx['created_by']}'"
        n, newest = q(
            f"SELECT count(*), max(created_at)::text FROM resolution.work_request "
            f"WHERE created_at > '{since}'::timestamptz AND {cond}"
        )[0]
        if n >= 1:
            ids = q(
                f"SELECT id, legacy_id FROM resolution.work_request "
                f"WHERE created_at > '{since}'::timestamptz AND {cond} "
                f"ORDER BY created_at DESC LIMIT 3"
            )
            ev = "; ".join(f"{a} {b}" for a, b in ids)
            return PASS, f"{n} row(s) landed since {since} with prefix '{prefix}'; newest: {ev}"
        return FAIL, (f"0 canonical rows since {since} with prefix '{prefix}'"
                      + (f" and created_by='{ctx['created_by']}'" if ctx.get("created_by") else "")
                      + " — repoint not observed")
    check.__name__ = cid
    return Check(cid, label, check)


def w1_cutover(q, ctx):
    """vision-srv must be DOWN (no listener); losm-host must be listening."""
    results = []
    code, detail = _probe(VISION_SRV_URL)
    results.append(("vision-srv " + VISION_SRV_URL,
                    PASS if code == "unreachable" else FAIL,
                    f"status={code}{detail} (expected down at cutover)"))
    code, detail = _probe(LOSM_HOST_URL)
    results.append(("losm-host " + LOSM_HOST_URL,
                    PASS if code == "listening" else FAIL,
                    f"status={code}{detail} (expected serving)"))
    ok = all(s == PASS for _, s, _ in results)
    return ((PASS if ok else FAIL),
            "; ".join(f"{n}: {s} ({d})" for n, s, d in results))


def _probe(url):
    """Reachability probe: ANY http response (any status) means a live
    listener — path-specific codes (404 on /) must not read as 'down'.
    Returns ('listening', ' (HTTP nnn)') or ('unreachable', ' (reason)')."""
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return "listening", f" (HTTP {r.status})"
    except urllib.error.HTTPError as e:
        return "listening", f" (HTTP {e.code})"
    except Exception as e:
        return "unreachable", f" ({type(e).__name__})"


TRANCHES = {
    "W2": [make_landing("W2a", "losm_store landing (W2)", "vision.work_requests:")],
    "W3": [make_landing("W3a", "conduit db_adapter landing (W3)", "nebula.work_requests_history:")],
    "W4": [make_landing("W4a", "cascade admission_subscriber landing (W4)", "nebula.work_requests_history:")],
    "W1": [Check("W1a", "vision-srv down + losm-host serving (cutover)", w1_cutover)],
}


# ── runner ────────────────────────────────────────────────────────────────────

def run(checks, q, ctx):
    results = []
    for c in checks:
        try:
            status, detail = c.fn(q, ctx)
        except ctx["UndefinedTable"] as e:
            status, detail = SKIP, f"surface absent ({e})"
        results.append({"id": c.id, "name": c.name, "status": status, "detail": detail})
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="W1-W4 writer-repoint verification battery")
    ap.add_argument("--tranche", choices=["W1", "W2", "W3", "W4"], help="tranche landing checks to include")
    ap.add_argument("--since", help="ISO timestamp: verify rows landed after the repoint's test write")
    ap.add_argument("--legacy-prefix", help="override the expected legacy_id prefix for landing checks")
    ap.add_argument("--created-by", help="narrow landing attribution (W3/W4 share the nebula prefix)")
    ap.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    args = ap.parse_args(argv)

    import psycopg2
    from psycopg2.errors import UndefinedTable

    checks = list(STRUCTURAL)
    if args.tranche:
        checks += TRANCHES[args.tranche]

    ctx = {"UndefinedTable": UndefinedTable, "since": args.since,
           "legacy_prefix": args.legacy_prefix, "created_by": args.created_by}
    try:
        conn = psycopg2.connect(DEFAULT_DSN)
        conn.autocommit = True
    except Exception as e:
        print(f"ENVIRONMENTAL: cannot reach PG ({e})", file=sys.stderr)
        return 2

    def q(sql):
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    results = run(checks, q, ctx)
    fails = [r for r in results if r["status"] == FAIL]

    if args.as_json:
        print(json.dumps({"results": results, "fail_count": len(fails)}, indent=1))
    else:
        w = max(len(r["id"] + " " + r["name"]) for r in results) + 2
        for r in results:
            print(f"{r['status']:<5} {r['id']:<4} {r['name']:<{w}} {r['detail']}")
        print(f"\n{len(results) - len(fails)}/{len(results)} passed"
              + (f" — {len(fails)} FAILED" if fails else " — battery green"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
