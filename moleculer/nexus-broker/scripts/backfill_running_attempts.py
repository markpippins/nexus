#!/usr/bin/env python3
"""Q5 bounded backfill (ruling ac38fa9b): terminal the orphaned RUNNING attempts.

Bound, exactly per the ruling: `status='RUNNING'` AND lease exists AND
`lease.expires_at < now()`. Data backfill only — no DDL, no fabricated
results: SUCCEEDED/FAILED/TIMED_OUT rows are untouched.

Usage:
    python3 scripts/backfill_running_attempts.py --dry-run   # default
    python3 scripts/backfill_running_attempts.py --apply
    python3 scripts/backfill_running_attempts.py --apply --expect 231

Connection comes from the broker .env (PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/
PG_DB_NAME) or NEXUS_PG_DSN directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BROKER_DIR = Path(__file__).resolve().parents[1]

# The ruling's exact bound. No other row matches this UPDATE.
BACKFILL_SQL = """
UPDATE execution.attempts a
SET status = 'TIMED_OUT',
    completed_at = NOW(),
    error = COALESCE(a.error, 'backfill (ac38fa9b Q5): lease expired with attempt still RUNNING')
WHERE a.status = 'RUNNING'
  AND EXISTS (
    SELECT 1 FROM execution.leases l
    WHERE l.id = a.lease_id
      AND l.expires_at < NOW()
  )
""".strip()

COUNT_SQL = """
SELECT count(*) AS n
FROM execution.attempts a
WHERE a.status = 'RUNNING'
  AND EXISTS (
    SELECT 1 FROM execution.leases l
    WHERE l.id = a.lease_id
      AND l.expires_at < NOW()
  )
""".strip()


def load_env() -> None:
    try:
        env_path = BROKER_DIR / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    except OSError:
        pass


def get_conn():
    import psycopg2  # deferred so --help works without the driver

    dsn = os.getenv("NEXUS_PG_DSN")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "localhost"),
        port=int(os.getenv("PG_PORT", "5432")),
        user=os.getenv("PG_USER", "pguser"),
        password=os.getenv("PG_PASSWORD", "pgpass"),
        dbname=os.getenv("PG_DB_NAME", "nexus"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="run the UPDATE for real")
    mode.add_argument("--dry-run", action="store_true", help="count only (default)")
    parser.add_argument("--expect", type=int, default=None, help="assert the matched count equals this before applying")
    args = parser.parse_args()

    load_env()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(COUNT_SQL)
            matched = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM execution.attempts WHERE status = 'RUNNING'")
            total_running = cur.fetchone()[0]

            plan = {
                "matched_bound": matched,
                "total_running": total_running,
                "mode": "apply" if args.apply else "dry-run",
            }

            if args.expect is not None and matched != args.expect:
                print(json.dumps({**plan, "assertion": "FAILED", "expected": args.expect}))
                print(
                    f"refusing to apply: bound matched {matched}, expected {args.expect} "
                    "(state changed since the ruling's recon?)",
                    file=sys.stderr,
                )
                return 1

            if not args.apply:
                print(json.dumps({**plan, "note": "dry-run; rerun with --apply to terminal these rows"}))
                return 0

            cur.execute(BACKFILL_SQL)
            updated = cur.rowcount
            conn.commit()

            cur.execute(COUNT_SQL)
            remaining = cur.fetchone()[0]
            print(
                json.dumps(
                    {
                        **plan,
                        "updated": updated,
                        "remaining_bound": remaining,
                        "assertion": "OK" if remaining == 0 else "FAILED",
                    }
                )
            )
            return 0 if remaining == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
