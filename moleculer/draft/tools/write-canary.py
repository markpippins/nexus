#!/usr/bin/env python3
"""Write-path canary: draft (incumbent :3140 vs twin :4170).

Ruling 59f8e2af (Q-C): the throwaway-schema approach satisfies the
shared-state rule — identical SQL through both twins inside the same live
DB; the schema is containment, not a different state. The ruling ADDS the
security-parity case: an X-Nexus-Internal-less probe must be rejected by
BOTH twins with envelope parity (Security Pass Alpha ported identically).

Requires NEXUS_INTERNAL_SECRET (fail-closed surface by contract).
"""
from __future__ import annotations

import os
import argparse
import json
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from write_canary_lib import (  # noqa: E402
    Canary, Case, RUN_ID, Twin, require_secret, deep_equal,
)

SCHEMA = "canary_wp"


def sql_body(sql: str):
    return lambda _t: {"engine": "postgres", "sql": sql}


def run_sql_both(canary: Canary, sql: str, expect_status: int = 200):
    ra = canary.twin_a().request("POST", "/api/db/query", sql_body(sql)(None))
    rb = canary.twin_b().request("POST", "/api/db/query", sql_body(sql)(None))
    if ra[0] != expect_status or rb[0] != expect_status:
        raise AssertionError(
            f"status A={ra[0]} B={rb[0]} expected {expect_status} for: {sql[:80]} | "
            f"A={json.dumps(ra[1], default=str)[:160]}"
        )
    ok, diff = deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"envelope mismatch for {sql[:60]!r}: {diff}")
    return ra, rb


def setup(canary: Canary) -> None:
    run_sql_both(canary, f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;")
    run_sql_both(canary, f"CREATE SCHEMA {SCHEMA};")
    run_sql_both(
        canary,
        f"CREATE TABLE {SCHEMA}.items (id serial PRIMARY KEY, name text, n integer);",
    )


def teardown(canary: Canary) -> None:
    # via the tested surface — the harness drops what the harness created
    canary.twin_a().request("POST", "/api/db/query", sql_body(
        f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;")(None))


def case_crud_roundtrip(canary: Canary, case: Case) -> None:
    """INSERT/SELECT/UPDATE/DELETE inside canary_wp, identical SQL both twins."""
    setup(canary)
    try:
        run_sql_both(canary, f"INSERT INTO {SCHEMA}.items (name, n) VALUES ('a', 1);")
        run_sql_both(canary, f"INSERT INTO {SCHEMA}.items (name, n) VALUES ('b', 2);")
        run_sql_both(canary, f"SELECT id, name, n FROM {SCHEMA}.items ORDER BY id;")
        run_sql_both(canary, f"UPDATE {SCHEMA}.items SET n = n + 10 WHERE name = 'a';")
        run_sql_both(canary, f"SELECT name, n FROM {SCHEMA}.items WHERE name = 'a';")
        run_sql_both(canary, f"DELETE FROM {SCHEMA}.items WHERE name = 'b';")
        run_sql_both(canary, f"SELECT COUNT(*) AS c FROM {SCHEMA}.items;")
    finally:
        teardown(canary)


def case_driver_error_parity(canary: Canary, case: Case) -> None:
    """Postgres error surfaces (code + message class) must match through both drivers."""
    setup(canary)
    try:
        ra = canary.twin_a().request(
            "POST", "/api/db/query", sql_body(f"SELECT * FROM {SCHEMA}.nope;")(None))
        rb = canary.twin_b().request(
            "POST", "/api/db/query", sql_body(f"SELECT * FROM {SCHEMA}.nope;")(None))
        if ra[0] != rb[0] or ra[0] < 400:
            raise AssertionError(f"driver error status A={ra[0]} B={rb[0]}")
        ok, diff = deep_equal(ra[1], rb[1])
        if not ok:
            raise AssertionError(f"driver error envelope mismatch: {diff}")
    finally:
        teardown(canary)


def case_security_parity_unauthenticated(canary: Canary, case: Case) -> None:
    """Ruling Q-C addition: secret-less probe rejected by BOTH twins, envelope parity."""
    anonymous_a = Twin("incumbent-anon", canary.port_a, secret=None)
    anonymous_b = Twin("twin-anon", canary.port_b, secret=None)
    ra = anonymous_a.request("GET", "/api/db/engines")
    rb = anonymous_b.request("GET", "/api/db/engines")
    if ra[0] < 400 or rb[0] < 400:
        canary.note_residue("an unauthenticated probe was ACCEPTED by a twin")
        raise AssertionError(f"security gate: A={ra[0]} B={rb[0]} (both must refuse)")
    ok, diff = deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"refusal envelope mismatch: {diff}")


def case_wrong_secret_parity(canary: Canary, case: Case) -> None:
    wrong_a = Twin("incumbent-wrong", canary.port_a, secret="definitely-wrong")
    wrong_b = Twin("twin-wrong", canary.port_b, secret="definitely-wrong")
    ra = wrong_a.request("GET", "/api/db/engines")
    rb = wrong_b.request("GET", "/api/db/engines")
    if ra[0] != 403 or rb[0] != 403:
        raise AssertionError(f"wrong-secret status A={ra[0]} B={rb[0]} (expect 403 both)")
    ok, diff = deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"403 envelope mismatch: {diff}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port-a", type=int, default=3140)
    ap.add_argument("--port-b", type=int, default=4170)
    args = ap.parse_args()

    secret = require_secret()
    canary = Canary("draft", args.port_a, args.port_b, secret=secret)
    canary.cases.extend([
        Case("security-parity-anon", case_security_parity_unauthenticated),
        Case("security-parity-wrong-secret", case_wrong_secret_parity),
        Case("crud-roundtrip-canary-wp", case_crud_roundtrip),
        Case("driver-error-parity", case_driver_error_parity),
    ])
    return canary.run()


if __name__ == "__main__":
    raise SystemExit(main())
