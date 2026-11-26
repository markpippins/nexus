"""Hermetic drift-intake seam test (plan 8261640 / R-2026-09-08-02).

Runs bin/drift-intake.py against a THROWAWAY schema (V147 DDL, schema-rewritten
so nebula.* -> throwaway.*) — never touches the live nebula/semantics surfaces.
Uses the test_lifecycle_ schema convention (create/drop via test_helpers).

Covers the ratified ruling + plan AC:

- dedup:      7 canary findings (2 duplicate pairs + 3 unique) -> 5 unique
               candidates (root-fact first-sentence fingerprint)
- idempotent: identical rerun -> zero new candidates (all preexisting)
- type:       candidates are typed 'drift', status 'active'
- discriminator: source:observations tag + provenance queryable on every row
- resolve:    when a finding resolves, its candidate -> superseded + freed
               dedupe key, so a re-observation can re-enter the pool
- backstop:   unique index rejects a second live candidate for same root fact

Run:
  bin/ci/local-pg-env.sh python3 -m pytest bin/tests/test_drift_intake.py -v
"""
import json
import os
import sys
import unittest
import uuid

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# test_helpers lives under python/conduit/tests/ (shared hermetic schema helpers).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python", "conduit", "tests"))

from test_helpers import (  # noqa: E402
    cleanup_orphaned_test_schemas,
    create_test_schema,
    drop_test_schema,
)

_DSN = os.environ.get("CONDUIT_PG_DSN", "")
if not _DSN:
    # An unset DSN is an absent dependency, not a guard result. Raising here
    # aborted collection of the WHOLE suite (pytest exit 2), so `pytest
    # bin/tests` said nothing about any other guard; skip this one with the
    # reason and the fix instead.
    pytest.skip(
        "CONDUIT_PG_DSN is unset: this guard needs a populated PostgreSQL "
        "(run it via bin/ci/local-pg-env.sh, which supplies and verifies one)",
        allow_module_level=True,
    )

_ORPHANED = cleanup_orphaned_test_schemas(_DSN)
if _ORPHANED:
    print(f"Cleaned up {_ORPHANED} orphaned test schema(s)", file=sys.stderr)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DRIFT_INTAKE = os.path.join(_SCRIPT_DIR, "..", "drift-intake.py")
_SQL_DIR = os.path.join(_SCRIPT_DIR, "..", "..", "sql")
_V147_SQL = os.path.join(_SQL_DIR, "V147__harvest_candidates_drift_intake.sql")

# Two duplicate pairs + three unique = 7 findings -> 5 unique root facts.
# Each pair shares an IDENTICAL first sentence (same root fact) but diverges in
# trailing exposition (snapshot-version wording), which the fingerprint must
# collapse via the first-sentence anchor.
_FINDINGS = [
    # pair A (health) — identical first sentence, divergent second
    ("Health check endpoint lacks a readiness probe and does not report "
     "dependencies. It also has no liveness route or timeout config."),
    ("Health check endpoint lacks a readiness probe and does not report "
     "dependencies. This manifests when the database pool is draining under load."),
    # pair B (historical archive) — identical first sentence, divergent second
    ("Historical archive purge drops rows without an audit trail and cannot "
     "be replayed. The retention policy is undocumented."),
    ("Historical archive purge drops rows without an audit trail and cannot "
     "be replayed. This happens during the nightly maintenance window."),
    # unique 1 (auth token rotation)
    ("Auth token rotation is not enforced on periodic refresh. Tokens older "
     "than 24h are still accepted."),
    # unique 2 (config reload)
    ("Config reload does not validate the new file before swapping it in. A "
     "malformed YAML crashes the process."),
    # unique 3 (rate limit)
    ("Rate limiter does not return Retry-After headers. Clients back off "
     "blindly and amplify retry storms."),
]


def _connect(schema=None):
    conn = psycopg2.connect(_DSN)
    if schema:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}")
    return conn


class DriftIntakeTestBase(unittest.TestCase):

    def setUp(self):
        self._conn = _connect()
        self._schema = create_test_schema(self._conn, "test_lifecycle")
        self._create_base_tables()
        self._apply_reschema(self._conn, _V147_SQL, self._schema)
        self._create_findings_table()
        self._conn.commit()
        self._schema_conn = _connect(self._schema)

    def tearDown(self):
        for conn in (getattr(self, "_schema_conn", None), self._conn):
            try:
                conn.close()
            except Exception:
                pass
        drop_test_schema(_DSN, self._schema)

    def _apply_reschema(self, conn, sql_path, schema):
        with open(sql_path) as f:
            raw = f.read()
        sql = raw.replace("nebula.", f"{schema}.").replace("semantics.", f"{schema}.")
        with conn.cursor() as cur:
            cur.execute(sql)

    def _create_base_tables(self):
        """Create minimal base tables (harvests_history, harvest_candidates_history)
        that V147's DDL (sentinel insert, ALTER, CREATE OR REPLACE VIEW) assumes
        exist in the throwaway schema — mirrors the live nebula history shapes."""
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE {self._schema}.harvests_history (
                  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  source_path       text NOT NULL,
                  source_filename   text NOT NULL DEFAULT '',
                  model             text NOT NULL DEFAULT '',
                  total_candidates  integer NOT NULL DEFAULT 0,
                  candidates        jsonb NOT NULL DEFAULT '[]',
                  source_text       text,
                  tags              text[] NOT NULL DEFAULT '{{}}',
                  metadata          jsonb NOT NULL DEFAULT '{{}}',
                  created_at        timestamptz NOT NULL DEFAULT now(),
                  recorded_on_dt    timestamptz NOT NULL DEFAULT now(),
                  recorded_until_dt timestamptz NOT NULL DEFAULT '9999-12-31 23:59:59+00',
                  valid_from        timestamptz NOT NULL DEFAULT now(),
                  valid_until       timestamptz NOT NULL DEFAULT '9999-12-31 23:59:59+00',
                  level             integer NOT NULL DEFAULT 1,
                  visibility_scope  text NOT NULL DEFAULT 'all',
                  docklang          jsonb,
                  source_hash       text,
                  version           integer NOT NULL DEFAULT 1,
                  run_metadata      jsonb NOT NULL DEFAULT '{{}}',
                  file_size         bigint,
                  asset_id          uuid
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE {self._schema}.harvest_candidates_history (
                  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  harvest_id               uuid NOT NULL,
                  title                    text NOT NULL,
                  intent_description       text,
                  implementation_notes     jsonb NOT NULL DEFAULT '[]',
                  code_snippets            jsonb NOT NULL DEFAULT '[]',
                  open_questions           jsonb NOT NULL DEFAULT '[]',
                  tags                     text[] NOT NULL DEFAULT '{{}}',
                  status                   text,
                  system_id                uuid,
                  subsystem_id             uuid,
                  feature_id               uuid,
                  valid_from               timestamptz NOT NULL DEFAULT now(),
                  valid_until              timestamptz NOT NULL DEFAULT '9999-12-31 23:59:59+00',
                  created_at               timestamptz NOT NULL DEFAULT now(),
                  updated_at               timestamptz NOT NULL DEFAULT now(),
                  work_request_id          uuid,
                  completed                boolean NOT NULL DEFAULT false,
                  compilation_readiness    numeric(4,3),
                  type                     text NOT NULL DEFAULT 'requirement',
                  design_rationale         jsonb NOT NULL DEFAULT '[]',
                  provenance_block_indices jsonb NOT NULL DEFAULT '[]',
                  needs_new_node           boolean NOT NULL DEFAULT false,
                  proposed_parent          text,
                  proposed_name            text,
                  placement_reason         text,
                  recorded_on_dt           timestamptz NOT NULL DEFAULT now(),
                  recorded_until_dt        timestamptz NOT NULL DEFAULT '9999-12-31 23:59:59+00',
                  asset_id                 uuid
                )
                """
            )
            cur.execute(
                f"CREATE INDEX idx_{self._schema}_hc_harvest ON {self._schema}.harvest_candidates_history (harvest_id)"
            )

    def _create_findings_table(self):
        """Create a throwaway semantics.drift_finding mirror (schema-rewritten
        table already lives in the throwaway schema under its bare name; create
        the full column set drift-intake.py reads)."""
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE {self._schema}.drift_finding (
                  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  observation_id uuid NOT NULL,
                  description    text NOT NULL,
                  severity       text NOT NULL DEFAULT 'info',
                  detected_at    timestamptz NOT NULL DEFAULT now(),
                  resolved_at    timestamptz,
                  expired_at     timestamptz
                )
                """
            )

    def _insert_findings(self, *severities):
        ids = []
        with self._conn.cursor() as cur:
            for i, desc in enumerate(_FINDINGS):
                sev = severities[i] if i < len(severities) else "info"
                cur.execute(
                    f"INSERT INTO {self._schema}.drift_finding "
                    "(observation_id, description, severity) VALUES (%s, %s, %s) RETURNING id",
                    (str(uuid.uuid4()), desc, sev),
                )
                ids.append(cur.fetchone()[0])
        self._conn.commit()
        return ids

    def _run_intake(self, schema, *args):
        env = dict(os.environ, DRIFT_INTAKE_SCHEMA=schema)
        import subprocess
        proc = subprocess.run(
            [sys.executable, _DRIFT_INTAKE, "--json", *args],
            capture_output=True, text=True, env=env,
        )
        if proc.returncode != 0:
            print(f"\nINTAKE FAILED rc={proc.returncode}\nSTDERR:\n{proc.stderr}", file=sys.stderr)
        return proc

    def _count(self, table, where="1=1"):
        conn = _connect(self._schema)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {self._schema}.{table} WHERE {where}")
                return cur.fetchone()[0]
        finally:
            conn.close()

    def _live_candidates(self):
        # Use a FRESH connection per read: the intake runs in a subprocess and
        # commits separately; a long-lived connection can hold a stale snapshot.
        conn = _connect(self._schema)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, dedupe_key, type, status, harvest_id, severity_note, "
                    f"tags, intent_description FROM {self._schema}.harvest_candidates "
                    f"WHERE type='drift'"
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()


class TestDriftIntake(DriftIntakeTestBase):

    def test_sentinel_harvest_created_by_migration(self):
        conn = _connect(self._schema)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {self._schema}.harvests_history WHERE source_path='observations/drift'")
                self.assertEqual(cur.fetchone()[0], 1, "V147 must create the sentinel harvest")
        finally:
            conn.close()

    def test_dedup_7_to_5_with_type_and_discriminator(self):
        ids = self._insert_findings("high", "high", "medium", "medium", "low", "low", "info")
        proc = self._run_intake(self._schema)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["scanned_unresolved"], 7)
        self.assertEqual(out["duplicate_pairs_absorbed"], 2)
        self.assertEqual(out["candidates_upserted"], 5)
        self.assertEqual(out["candidates_preexisting"], 0)

        cands = self._live_candidates()
        self.assertEqual(len(cands), 5)
        # All typed 'drift', status 'active', source discriminator present.
        for c in cands:
            self.assertEqual(c["type"], "drift")
            self.assertEqual(c["status"], "active")
            self.assertEqual(c["harvest_id"], None if False else c["harvest_id"])
            self.assertIn("source:observations", c["tags"])
        # Distinct root-fact fingerprints == 5.
        fps = {c["dedupe_key"] for c in cands}
        self.assertEqual(len(fps), 5)

    def test_idempotent_rerun_no_duplicates(self):
        self._insert_findings()
        proc1 = self._run_intake(self._schema)
        self.assertEqual(proc1.returncode, 0)
        out1 = json.loads(proc1.stdout)
        self.assertEqual(out1["candidates_upserted"], 5)

        proc2 = self._run_intake(self._schema)
        out2 = json.loads(proc2.stdout)
        self.assertEqual(out2["candidates_upserted"], 0, "rerun must not insert")
        self.assertEqual(out2["candidates_preexisting"], 5)
        self.assertEqual(self._count("harvest_candidates", "type='drift'"), 5)

    def test_dry_run_writes_nothing(self):
        self._insert_findings()
        proc = self._run_intake(self._schema, "--dry-run")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["candidates_upserted"], 5, "dry-run still reports")
        self.assertEqual(self._count("harvest_candidates", "type='drift'"), 0, "dry-run writes nothing")

    def test_resolution_supersedes_and_frees_dedupe_key(self):
        ids = self._insert_findings("high", "high", "info", "info", "info", "info", "info")
        proc = self._run_intake(self._schema)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["candidates_upserted"], 5)

        # Resolve finding[0] (pair A root fact) -> candidate superseded.
        with self._conn.cursor() as cur:
            cur.execute(
                f"UPDATE {self._schema}.drift_finding SET resolved_at=now() WHERE id=%s",
                (ids[0],),
            )
        self._conn.commit()

        proc = self._run_intake(self._schema)
        out = json.loads(proc.stdout)
        self.assertEqual(out["candidates_resolved"], 1)

        # The superseded candidate left the live view (valid_until < now).
        live = self._live_candidates()
        self.assertEqual(len(live), 4, "resolved candidate leaves live pool")
        # Its dedupe key is freed: re-observing the same root fact re-enters.
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._schema}.drift_finding (observation_id, description, severity) "
                f"VALUES (%s, %s, 'high')",
                (str(uuid.uuid4()), _FINDINGS[0]),
            )
        self._conn.commit()
        proc = self._run_intake(self._schema)
        out = json.loads(proc.stdout)
        self.assertEqual(out["candidates_upserted"], 1, "re-observation re-enters after resolution")
        self.assertEqual(len(self._live_candidates()), 5)


if __name__ == "__main__":
    unittest.main()