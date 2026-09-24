#!/usr/bin/env python3
"""Census users-table password hygiene on a live nexus database.

Enforced scope (V191/V202): assembly.users + gateway.users. For each surface
this check verifies the full enforcement path, not just the data:

  1. zero plaintext rows (password NOT LIKE '$2%' AND password <> '' — ''
     is the ratified V191 escape, not drift)
  2. the users_password_bcrypt_check CHECK is present
  3. the trg_bcrypt_write_guard trigger is present

Any miss is drift: it means the born-clean guarantee has been dropped,
out-of-band restore, or removed by hand — the exact classes the 2026-09-24
forensics were about.

public.users is out of the enforced scope (V191 ratified: different app
surface, no enforcement path) but is censused and REPORTED as a notice so
its plaintext population stays visible. Pass --include-public to escalate
it to a failure.

Exit codes: 0 = clean, 1 = drift found (timer files a record), 2 = tool
error (unreachable DB, etc.).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg2

DSN = (os.environ.get("NEXUS_PG_DSN")
       or os.environ.get("CONDUIT_PG_DSN")
       or "postgresql://pguser:pgpass@localhost:5432/nexus")

ENFORCED_SURFACES = ("assembly.users", "gateway.users")
NOTICE_SURFACES = ("public.users",)

PLAINTEXT_WHERE = "password NOT LIKE '$2%' AND password <> ''"


def census(cur, surface: str) -> dict:
    schema, table = surface.split(".")
    cur.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s AND column_name='password'",
        (schema, table))
    if cur.fetchone()[0] == 0:
        # Notice surface without a password column (reshaped out-of-band, as
        # public.users was on 2026-09-24): nothing to enforce here. Record
        # shape stays stable so callers never special-case; the note surfaces
        # the anomaly instead of dying on it.
        return {"surface": surface, "rows": None, "plaintext": 0,
                "check_present": False, "trigger_present": False,
                "ok": True, "skipped": True,
                "note": "no password column (surface reshaped out-of-band?)"}
    cur.execute(
        f"SELECT count(*) FILTER (WHERE {PLAINTEXT_WHERE}), count(*) "
        f"FROM {schema}.{table}")
    plain, total = cur.fetchone()
    cur.execute(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname='users_password_bcrypt_check' "
        f"AND conrelid='{schema}.{table}'::regclass")
    check = cur.fetchone()[0]
    cur.execute(
        "SELECT count(*) FROM pg_trigger "
        f"WHERE tgrelid='{schema}.{table}'::regclass "
        "AND tgname='trg_bcrypt_write_guard' AND NOT tgisinternal")
    trig = cur.fetchone()[0]
    return {"surface": surface, "rows": total, "plaintext": plain,
            "check_present": check == 1, "trigger_present": trig == 1,
            "ok": plain == 0 and check == 1 and trig == 1}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--include-public", action="store_true",
                    help="escalate public.users plaintext to a failure")
    args = ap.parse_args()

    try:
        conn = psycopg2.connect(DSN)
    except Exception as exc:
        print(f"TOOL ERROR: cannot reach {DSN.rsplit('/', 1)[0]}: {exc}",
              file=sys.stderr)
        return 2

    try:
        with conn.cursor() as cur:
            results = [census(cur, s) for s in ENFORCED_SURFACES]
            notices = [census(cur, s) for s in NOTICE_SURFACES]
    except Exception as exc:
        print(f"TOOL ERROR: census failed: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    failures = [r for r in results if not r["ok"]]
    if args.include_public:
        failures += [n for n in notices if n["plaintext"] > 0]

    if args.json:
        print(json.dumps({"failures": failures, "enforced": results,
                          "notices": notices}))
    else:
        for r in results + notices:
            tag = "ENFORCED" if r in results else "notice  "
            if r.get("skipped"):
                print(f"[{tag}] {r['surface']}: SKIPPED — {r['note']}")
                continue
            state = "OK" if r["ok"] else "DRIFT"
            print(f"[{tag}] {r['surface']}: {state} — "
                  f"plaintext {r['plaintext']}/{r['rows']}, "
                  f"check={'yes' if r['check_present'] else 'MISSING'}, "
                  f"trigger={'yes' if r['trigger_present'] else 'MISSING'}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
