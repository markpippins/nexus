#!/usr/bin/env python3
"""E2E: V189 topology asset-spine pre-stage — real migration, throwaway DB.

wr-conf house pattern (V179/V184/V185/V186 companions). The REAL
sql/V189__topology_asset_spine.sql applies to a throwaway database carrying
the pre-V189 skeleton (semantics.canonical_asset live shape; registry.servers,
terrain.servers, terrain.service_endpoints minimal live shapes), and the full
contract is exercised against live constraint behavior — no mocks on the DB path:

  - PREFLIGHT-001: unmapped live kind REFUSES loudly (no silent vocabulary)
  - PREFLIGHT-003: double-apply refused (asset_id already present)
  - Stage 1: chk_canonical_asset_kind installed; the 29 contract kinds pass;
    a bogus kind refuses live
  - Stage 2: asset_id + origin columns on all three surfaces (nullable
    asset_id, NOT NULL origin_source with surface default), FK into
    semantics.canonical_asset(id), unique partial indexes enforce
    one-asset-one-row per surface
  - absent-is-honest: pre-existing rows backfilled NULL and still valid

Suite creates and drops its own throwaway database; no production DB.

Run:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_v189_topology_spine_e2e.py -v
"""

import os
import unittest
import uuid

import psycopg2

_REPO = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V189_PATH = os.path.join(_REPO, "sql", "V189__topology_asset_spine.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

# The 29-kind Stage-1 vocabulary (26 live + host/database/broker) — must match
# the migration's CHECK exactly.
CONTRACT_KINDS = [
    'agent_record:analysis', 'agent_record:architecture_note',
    'agent_record:assessment', 'agent_record:decision',
    'agent_record:engineering_log', 'agent_record:inspection',
    'agent_record:prompt', 'agent_record:report', 'agent_record:response',
    'candidate', 'cli_tool', 'concept', 'document', 'feature', 'file',
    'implementation_plan', 'knowledge_entity', 'mcp_server', 'plan',
    'requirement', 'service', 'session_log', 'subsystem', 'system',
    'transcript', 'work_request',
    'host', 'database', 'broker',
]

SKELETON_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── semantics.canonical_asset (live shape, PRE-V189: no kind CHECK) ────────
CREATE SCHEMA semantics;
CREATE TABLE semantics.canonical_asset (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_asset_id text NOT NULL,
    asset_kind         text NOT NULL,
    canonical_key      jsonb,
    source_hash        text,
    content_hash       text,
    validity_start     timestamptz,
    validity_end       timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    expired_at         timestamptz
);

-- ── registry.servers (declared membership, minimal live shape) ─────────────
CREATE SCHEMA registry;
CREATE TABLE registry.servers (
    id           bigserial PRIMARY KEY,
    hostname     varchar(255),
    ip_address   varchar(255),
    status       varchar(255),
    active_flag  boolean DEFAULT true,
    created_at   timestamp DEFAULT now(),
    updated_at   timestamp DEFAULT now()
);

-- ── terrain.servers (observed machine state, minimal live shape) ───────────
CREATE SCHEMA terrain;
CREATE TABLE terrain.servers (
    id           bigserial PRIMARY KEY,
    hostname     varchar(255),
    ip_address   varchar(255),
    os           varchar(255),
    status       varchar(255),
    active_flag  boolean DEFAULT true
);

-- ── terrain.service_endpoints (observed registrations, live shape) ─────────
CREATE TABLE terrain.service_endpoints (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    host            varchar(255),
    instance        varchar(255),
    ip              inet,
    port            integer,
    scheme          varchar(255),
    status          varchar(255),
    unit            varchar(255),
    last_heartbeat  timestamptz
);
"""

SEED_SQL = """
INSERT INTO semantics.canonical_asset (canonical_asset_id, asset_kind) VALUES
    ('asset:nexus:terrain_mcp_servers:5', 'mcp_server'),
    ('asset:nexus:services:nexus-srv',    'service'),
    ('asset:nexus:concepts:workrequest',  'concept'),
    ('asset:nexus:wr:7dbf3112',           'work_request');

INSERT INTO registry.servers (hostname, ip_address, status) VALUES
    ('titanium', '192.168.1.10', 'ONLINE'),
    ('helium',   '192.168.1.11', 'ONLINE');

INSERT INTO terrain.servers (hostname, ip_address, os, status) VALUES
    ('titanium', '192.168.1.10', 'linux', 'ONLINE');

INSERT INTO terrain.service_endpoints (host, instance, ip, port, scheme, status, unit) VALUES
    ('titanium', 'primary', '192.168.1.10', 3101, 'http', 'up', 'nebula-srv'),
    ('titanium', 'primary', '192.168.1.10', 3400, 'http', 'up', 'tackle-mcp');
"""


def _apply_file(cur, path):
    with open(path) as f:
        cur.execute(f.read())


class ThrowawayDB:
    def __init__(self, name):
        self.name = name
        self.admin = psycopg2.connect(DSN)
        self.admin.autocommit = True
        with self.admin.cursor() as c:
            # clear lingering backends so DROP never blocks on stale locks
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                      "WHERE datname=%s AND pid <> pg_backend_pid()", (name,))
            c.execute(f"DROP DATABASE IF EXISTS {name}")
            c.execute(f"CREATE DATABASE {name}")
        dsn = DSN.rsplit("/", 1)[0]
        self.conn = psycopg2.connect(f"{dsn}/{name}")
        self.conn.autocommit = True

    def normalize(self):
        """Server-side ROLLBACK to clear the aborted implicit transaction
        left by a failed multi-statement batch. psycopg2's conn.rollback()
        is a NO-OP under autocommit=True (the V186 harness lesson), so the
        recovery must go to the server as raw SQL."""
        c = self.conn.cursor()
        try:
            c.execute("ROLLBACK")
        finally:
            c.close()

    def cursor(self):
        return self.conn.cursor()

    def close(self):
        try:
            self.conn.close()
        finally:
            with self.admin.cursor() as c:
                c.execute(f"DROP DATABASE IF EXISTS {self.name}")
            self.admin.close()


class V189TopologySpineE2E(unittest.TestCase):
    """One throwaway DB drives the whole sequence: gates first (refusal +
    zero-mutation proof), then the clean apply, then the contract."""

    @classmethod
    def setUpClass(cls):
        cls.db = ThrowawayDB("nexus_v189_spine_test")
        with cls.db.cursor() as c:
            c.execute(SKELETON_SQL)
            c.execute(SEED_SQL)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def _column_exists(self, cur, table, column):
        schema = table.split(".")[0]
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
            (schema, table.split(".")[1], column))
        return cur.fetchone()[0] == 1

    def test_01_preflight_001_unmapped_kind_refuses_loud(self):
        """A live kind outside the vocabulary aborts with a NAMED gate."""
        with self.db.cursor() as c:
            c.execute("INSERT INTO semantics.canonical_asset "
                      "(canonical_asset_id, asset_kind) VALUES "
                      "('asset:nexus:weird:1', 'quantum_widget')")
            with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
                _apply_file(c, V189_PATH)
            self.db.normalize()  # clear the aborted implicit transaction
            self.assertIn("V189-PREFLIGHT-001", str(ctx.exception))
            self.assertIn("quantum_widget", str(ctx.exception))
            # zero mutation: no CHECK, no columns, seed rows untouched
            c.execute("SELECT count(*) FROM pg_constraint WHERE conname="
                      "'chk_canonical_asset_kind'")
            self.assertEqual(c.fetchone()[0], 0)
            self.assertFalse(self._column_exists(c, "registry.servers", "asset_id"))
            c.execute("DELETE FROM semantics.canonical_asset "
                      "WHERE asset_kind='quantum_widget'")

    def test_02_clean_apply_succeeds(self):
        with self.db.cursor() as c:
            _apply_file(c, V189_PATH)
            c.execute("SELECT count(*) FROM pg_constraint WHERE conname="
                      "'chk_canonical_asset_kind'")
            self.assertEqual(c.fetchone()[0], 1)

    def test_03_preflight_003_double_apply_refused(self):
        with self.db.cursor() as c:
            with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
                _apply_file(c, V189_PATH)
            self.db.normalize()
            self.assertIn("V189-PREFLIGHT-003", str(ctx.exception))

    def test_04_kind_check_all_29_pass_bogus_refused(self):
        """Every contract kind inserts; a 30th refuses live (23514)."""
        with self.db.cursor() as c:
            for kind in CONTRACT_KINDS:
                c.execute(
                    "INSERT INTO semantics.canonical_asset "
                    "(canonical_asset_id, asset_kind) VALUES (%s, %s)",
                    (f"asset:test:v189:{kind}", kind))
            with self.assertRaises(psycopg2.errors.CheckViolation) as ctx:
                c.execute("INSERT INTO semantics.canonical_asset "
                          "(canonical_asset_id, asset_kind) VALUES "
                          "('asset:test:v189:bogus', 'quantum_widget')")
            self.db.normalize()
            self.assertEqual(ctx.exception.pgcode, "23514")

    def test_05_stage2_columns_present_with_honest_defaults(self):
        with self.db.cursor() as c:
            for table, default in [("registry.servers", "registry.servers"),
                                   ("terrain.servers", "terrain.servers"),
                                   ("terrain.service_endpoints",
                                    "terrain.service_endpoints")]:
                self.assertTrue(self._column_exists(c, table, "asset_id"))
                self.assertTrue(self._column_exists(c, table, "origin_source"))
                self.assertTrue(self._column_exists(c, table, "origin_ref"))
                c.execute(f"SELECT DISTINCT origin_source FROM {table}")
                self.assertEqual(c.fetchone()[0], default)
            # absent-is-honest: every pre-existing row backfilled NULL
            c.execute("SELECT count(*) FROM registry.servers "
                      "WHERE asset_id IS NOT NULL")
            self.assertEqual(c.fetchone()[0], 0)
            c.execute("SELECT count(*) FROM terrain.service_endpoints "
                      "WHERE asset_id IS NOT NULL")
            self.assertEqual(c.fetchone()[0], 0)

    def test_06_fk_reality_and_one_asset_one_row(self):
        """Binding requires a real spine row (23503); a second row binding
        the same asset refuses via the unique partial index (23505)."""
        with self.db.cursor() as c:
            c.execute("SELECT id FROM semantics.canonical_asset "
                      "WHERE canonical_asset_id='asset:nexus:services:nexus-srv'")
            asset_id = c.fetchone()[0]
            fake_id = str(uuid.uuid4())

            with self.assertRaises(psycopg2.errors.ForeignKeyViolation):
                c.execute("UPDATE registry.servers SET asset_id=%s "
                          "WHERE hostname='titanium'", (fake_id,))
            self.db.normalize()

            c.execute("SELECT count(*) FROM registry.servers "
                      "WHERE hostname='titanium' AND asset_id IS NULL")
            self.assertEqual(c.fetchone()[0], 1)  # refusal rolled back

            c.execute("UPDATE registry.servers SET asset_id=%s, "
                      "origin_ref='inventory.yml:all.hosts.titanium' "
                      "WHERE hostname='titanium'", (asset_id,))
            c.execute("SELECT asset_id, origin_ref FROM registry.servers "
                      "WHERE hostname='titanium'")
            row = c.fetchone()
            self.assertEqual(row[0], asset_id)
            self.assertEqual(row[1], "inventory.yml:all.hosts.titanium")

            c.execute("UPDATE terrain.servers SET asset_id=%s "
                      "WHERE hostname='titanium'", (asset_id,))
            with self.assertRaises(psycopg2.errors.UniqueViolation):
                c.execute("INSERT INTO terrain.servers (hostname, asset_id) "
                          "VALUES ('titanium-mirror', %s)", (asset_id,))
            self.db.normalize()

            # endpoint binding to a DIFFERENT asset is fine (per-surface uq)
            c.execute("SELECT id FROM semantics.canonical_asset "
                      "WHERE canonical_asset_id='asset:nexus:terrain_mcp_servers:5'")
            mcp_id = c.fetchone()[0]
            c.execute("UPDATE terrain.service_endpoints SET asset_id=%s, "
                      "origin_ref='endpoint-register:3400' "
                      "WHERE unit='tackle-mcp'", (mcp_id,))
            c.execute("SELECT count(*) FROM terrain.service_endpoints "
                      "WHERE asset_id IS NOT NULL")
            self.assertEqual(c.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
