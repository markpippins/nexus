#!/usr/bin/env python3
"""W-B3 (plan 8261675): §9.2 throwaway seed-DB proof operator.

Creates exactly one `nexus_wb3_seed_<epoch>` database, builds its schema from
the repository's own schema-only bootstrap plus `initDb()/createSchema()`, runs
the committed TS harness (which drives the real TicketExpirySweeper), verifies
A1–A7, and drops the database. The live `nexus` DB is read-only; no DSN is ever
written to an artifact or to stdout.

Usage:
    bin/wb3_seeded_proof.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2

REPO = Path(__file__).resolve().parent.parent
ARTIFACT = REPO / "docs/promotion-flow/evidence/wb3-seeded-proof-8261675.json"
BOOTSTRAP = REPO / "sql/ci-bootstrap/nexus-ci-bootstrap.sql"
RUNNER_REL = "typescript/conduit-mcp/scripts/wb3-seeded-proof-runner.ts"
RUNNER = REPO / RUNNER_REL
MCP_DIR = REPO / "typescript/conduit-mcp"
DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"
GRANT_REF = "d77594c2-e266-467e-81d0-dc6ca207c581"
GRANT_START = datetime(2026, 11, 19, 1, 19, tzinfo=timezone.utc)
GRANT_END = datetime(2026, 11, 20, 1, 19, tzinfo=timezone.utc)


def _scrub(text: str, secrets: list[str]) -> str:
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[REDACTED]")
    return re.sub(r"postgresql://[^:\s/]+:[^@\s]+@", "postgresql://[REDACTED]@", out)


def _admin_dsn(base_dsn: str, database: str) -> str:
    parts = urlsplit(base_dsn)
    netloc = parts.netloc
    if "@" in netloc:
        credentials, host = netloc.rsplit("@", 1)
    else:
        credentials, host = "", netloc
    return urlunsplit((parts.scheme, f"{credentials}@{host}", f"/{database}", "", ""))


def _exec(cmd: list[str], *, cwd: Path, env: dict[str, str], secrets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    return proc.returncode, _scrub(proc.stdout + proc.stderr, secrets)


def _drop(dbname: str, base_dsn: str) -> str:
    conn = psycopg2.connect(_admin_dsn(base_dsn, "postgres"))
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        return "dropped"
    finally:
        conn.close()


def main() -> int:
    now = datetime.now(timezone.utc)
    if not (GRANT_START <= now <= GRANT_END):
        raise SystemExit(
            f"§9.2 grant window ({GRANT_START.isoformat()}..{GRANT_END.isoformat()}) has lapsed; "
            f"no seed DB created at {now.isoformat()}"
        )

    base_dsn = os.environ.get("CONDUIT_PG_DSN", DEFAULT_DSN)
    secrets = [base_dsn]
    parts = urlsplit(base_dsn)
    password = ""
    if parts.netloc and "@" in parts.netloc:
        credentials = parts.netloc.rsplit("@", 1)[0]
        password = credentials.split(":", 1)[1] if ":" in credentials else ""

    admin = psycopg2.connect(_admin_dsn(base_dsn, "postgres"))
    try:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database WHERE datname LIKE 'nexus_wb3_seed%'")
            existing = [r[0] for r in cur.fetchall()]
            if existing:
                raise SystemExit(
                    "refusing to create another seed DB; leftover seed database(s): "
                    + ", ".join(existing)
                )
            seed_db = f"nexus_wb3_seed_{int(time.time())}"
            cur.execute(f'CREATE DATABASE "{seed_db}"')
    finally:
        admin.close()

    seed_dsn = _admin_dsn(base_dsn, seed_db)
    secrets.append(seed_dsn)
    child_env = os.environ.copy()
    child_env.update(
        {
            "PGPASSWORD": password,
            "CONDUIT_PG_DSN": seed_dsn,
            "WB3_NEXUS_DSN": base_dsn,
        }
    )

    result: dict[str, object] = {
        "artifact": "WB3-SEEDED-PROOF-RUN",
        "grant_ref": GRANT_REF,
        "seed_database": seed_db,
        "run_at": now.isoformat(),
        "bootstrap": "not attempted",
        "runner": "not attempted",
        "teardown": "not attempted",
    }
    try:
        rc, log = _exec(
            [
                "psql",
                "-h",
                parts.hostname or "localhost",
                "-p",
                str(parts.port or 5432),
                "-U",
                (parts.username or "pguser"),
                "-d",
                seed_db,
                "-v",
                "ON_ERROR_STOP=1",
                "-q",
                "-f",
                str(BOOTSTRAP),
            ],
            cwd=REPO,
            env=child_env,
            secrets=secrets,
        )
        result["bootstrap"] = {"exit_code": rc, "log": log[-4000:]}
        if rc != 0:
            result["status"] = "blocked"
            result["error"] = "schema bootstrap failed; seed DB will be dropped"
            return 1

        rc, log = _exec(
            ["npx", "tsx", str(RUNNER.relative_to(MCP_DIR))],
            cwd=MCP_DIR,
            env=child_env,
            secrets=secrets,
        )
        result["runner"] = {"exit_code": rc, "log": log[-6000:]}
        if rc != 0:
            result["status"] = "blocked"
            result["error"] = "seeded-proof runner failed; see runner.log"
            return 1

        proof = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        failures = [k for k, v in proof.get("assertions", {}).items() if not v.get("pass")]
        result["status"] = "pass" if not failures and proof.get("teardown", {}).get("dropped") else "fail"
        result["assertions"] = {
            k: v.get("pass") for k, v in proof.get("assertions", {}).items()
        }
        result["failures"] = failures
        result["teardown"] = proof.get("teardown")
        return 0 if result["status"] == "pass" else 1
    except Exception as exc:  # noqa: BLE001 - operator script must always clean up
        result["status"] = "blocked"
        result["error"] = _scrub(f"{type(exc).__name__}: {exc}", secrets)
        return 1
    finally:
        result["teardown_checked_at"] = datetime.now(timezone.utc).isoformat()
        try:
            admin2 = psycopg2.connect(_admin_dsn(base_dsn, "postgres"))
            try:
                admin2.autocommit = True
                with admin2.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM pg_database WHERE datname = %s", (seed_db,)
                    )
                    exists = cur.fetchone() is not None
            finally:
                admin2.close()
            if exists:
                result["final_cleanup"] = _drop(seed_db, base_dsn)
            else:
                result["final_cleanup"] = "already absent (runner dropped it)"
        except Exception as exc:  # noqa: BLE001
            result["final_cleanup"] = "FAILED: " + _scrub(str(exc), secrets)
            result["status"] = "blocked"
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
