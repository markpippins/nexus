#!/usr/bin/env python3
"""E2E: Wave 2 grant files — the six ratified-as-designed roles (wr-conf-030).

critic, epistemologist, devops, sysadmin, operator, sound-technician
(architect decision c141dd7a, Wave 2). House throwaway-DB pattern:
skeleton pre-V175 world + six capability-empty targets (the live shape),
V175 repair, then each REAL grant file as a grant event.

Pinned per ratified design:
  - critic: {} domains, create-only questions, max 5
  - epistemologist: {} domains, create+resolve, max 5,
    out_of_domain_resolution trigger (the only Wave-2 authority)
  - devops/sysadmin/operator/sound-technician: one domain each,
    no flags (defaults-false), escalation {architect}
  - all: verify/greenlight/agenda FALSE; chain closed=1/open=1,
    handoff exact; repeatability = second grant event; missing-role
    refusal atomic
"""
import os
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V175_PATH = os.path.join(_REPO_ROOT, "sql", "V175__roles_history_close_then_insert_repair.sql")
V180_PATH = os.path.join(_REPO_ROOT, "sql", "V180__applied_grants_preflight.sql")
GRANTS_DIR = os.path.join(_REPO_ROOT, "sql", "grants")

ROLES = ["critic", "epistemologist", "devops", "sysadmin", "operator",
         "sound-technician"]
GRANT_PATHS = {r: os.path.join(GRANTS_DIR, f"{r}-grant-v0.1.sql") for r in ROLES}

# Ratified expected values: (domains, cq, rq, moq, triggers)
EXPECTED = {
    "critic":           ([], True,  False, 5, []),
    "epistemologist":   ([], True,  True,  5, ["out_of_domain_resolution"]),
    "devops":           (["environment_operations"], False, False, 0, []),
    "sysadmin":         (["host_operations"], False, False, 0, []),
    "operator":         (["fleet_operations"], False, False, 0, []),
    "sound-technician": (["audio_feedback"], False, False, 0, []),
}

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")
SENTINEL = "9999-12-31 00:00:00+00"


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_wave2_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self.conn = None

    def __enter__(self):
        admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            cur.execute(f'CREATE DATABASE "{self.dbname}"')
        admin.close()
        self.conn = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.dbname)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute(SKELETON_SQL)
            cur.execute(SEED_SQL)
        return self

    def __exit__(self, *exc):
        try:
            if self.conn:
                self.conn.close()
        finally:
            admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            admin.close()

    def sql(self, stmt, params=None):
        with self.conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall() if cur.description else None
        if rows and len(rows) == 1 and len(rows[0]) == 1:
            return rows[0][0]
        return rows

    def apply_file(self, path):
        with open(path) as fh:
            self.sql(fh.read())

    def chain(self, role):
        rows = self.sql("""
SELECT count(*) FILTER (WHERE valid_until <> %s::timestamptz),
       count(*) FILTER (WHERE valid_until =  %s::timestamptz)
  FROM nebula.roles_history WHERE name = %s
""", (SENTINEL, SENTINEL, role))
        return rows[0]

    def open_row(self, role):
        rows = self.sql("""
SELECT owns_domains, can_verify_work_requests, can_create_questions,
       can_resolve_questions, can_greenlight, can_create_agendas,
       max_open_questions, escalates_to, escalation_triggers
  FROM nebula.roles_history
 WHERE name = %s AND valid_until = %s::timestamptz
""", (role, SENTINEL))
        return rows[0]

    def handoff_exact(self, role):
        return self.sql("""
SELECT count(*) = 0 FROM nebula.roles_history o
JOIN nebula.roles_history c ON c.name = o.name
  AND c.valid_until <> %s::timestamptz
  AND c.valid_until = (SELECT max(valid_until) FROM nebula.roles_history
                       WHERE name = o.name AND valid_until <> %s::timestamptz)
WHERE o.name = %s AND o.valid_until = %s::timestamptz
  AND o.valid_from IS DISTINCT FROM c.valid_until
""", (SENTINEL, SENTINEL, role, SENTINEL))


SKELETON_SQL = f"""
CREATE SCHEMA nebula;
CREATE SCHEMA tackle;

CREATE TABLE nebula.roles_history (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    text NOT NULL,
    display_name            text,
    description             text,
    owns_domains            text[] DEFAULT '{{}}',
    can_greenlight          boolean DEFAULT false,
    can_create_questions    boolean DEFAULT false,
    can_create_agendas      boolean DEFAULT false,
    can_resolve_questions   boolean DEFAULT false,
    can_verify_work_requests boolean DEFAULT false,
    max_open_questions      integer,
    requires_approval_from  text[],
    cron_enabled            boolean DEFAULT false,
    cron_expression         text,
    cron_description        text,
    escalates_to            text[] DEFAULT '{{}}',
    escalation_triggers     text[] DEFAULT '{{}}',
    level_filter_primary    text,
    level_filter_allowed    text,
    visibility_scope        text[] DEFAULT '{{}}',
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_until             timestamptz NOT NULL DEFAULT '{SENTINEL}'::timestamptz,
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT '{SENTINEL}'::timestamptz,
    CONSTRAINT roles_name_key UNIQUE (name)
);

CREATE TABLE tackle.role_leases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role text NOT NULL, channel text, model text,
    status text NOT NULL DEFAULT 'ACTIVE',
    acquired_at timestamptz DEFAULT now(), expires_at timestamptz,
    released_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tackle.system_logs (
    id text PRIMARY KEY, timestamp timestamptz NOT NULL DEFAULT now(),
    level text NOT NULL, category text NOT NULL, message text NOT NULL,
    source text, details jsonb
);

CREATE VIEW nebula.roles AS
SELECT * FROM nebula.roles_history
WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
  AND now() >= valid_from AND now() < valid_until;
"""

SEED_SQL = "".join(
    f"INSERT INTO nebula.roles_history (name, display_name, description) "
    f"VALUES ('{r}', '{r.title()}', 'Ratified role vocabulary');\n"
    for r in ROLES
)


class Wave2GrantsE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__)
        self.db.apply_file(V175_PATH)
        self.db.apply_file(V180_PATH)  # rediff gate (GRANT-APPLIED)

    def test_all_six_grant_events(self):
        for role in ROLES:
            self.db.apply_file(GRANT_PATHS[role])
            closed, opened = self.db.chain(role)
            self.assertEqual((1, 1), (closed, opened), role)
            self.assertTrue(self.db.handoff_exact(role), role)
            dom, ver, cq, rq, gl, agenda, moq, esc, trig = self.db.open_row(role)
            exp_dom, exp_cq, exp_rq, exp_moq, exp_trig = EXPECTED[role]
            self.assertEqual(sorted(exp_dom), sorted(dom), role)
            self.assertEqual(exp_cq, cq, role)
            self.assertEqual(exp_rq, rq, role)
            self.assertEqual(exp_moq, moq, role)
            self.assertEqual(sorted(exp_trig), sorted(trig), role)
            # Wave-2 invariants: never granted.
            self.assertFalse(ver, role)
            self.assertFalse(gl, role)
            self.assertFalse(agenda, role)
            self.assertEqual(["architect"], list(esc), role)

    def test_reapply_same_spec_refuses_GRANT_APPLIED(self):
        """V180 rediff gate: an identical re-apply is a loud no-op, not a
        second grant event — the self-idempotence doctrine (R1 bae6f566)."""
        self.db.apply_file(GRANT_PATHS["critic"])
        try:
            self.db.apply_file(GRANT_PATHS["critic"])
        except psycopg2.Error as exc:
            self.assertIn("GRANT-APPLIED", str(exc))
        else:
            self.fail("identical re-apply must refuse with GRANT-APPLIED")
        try:
            self.db.sql("ROLLBACK")   # clear stranded aborted tx (gotcha #5)
        except psycopg2.Error:
            pass
        closed, opened = self.db.chain("critic")
        self.assertEqual((1, 1), (closed, opened), "no redundant event minted")
        self.assertTrue(self.db.handoff_exact("critic"))

    def test_missing_role_refuses_atomically(self):
        self.db.sql("DELETE FROM nebula.roles_history WHERE name='devops'")
        try:
            self.db.apply_file(GRANT_PATHS["devops"])
        except psycopg2.Error:
            pass
        else:
            self.fail("grant against a missing role must fail loudly")
        try:
            self.db.sql("ROLLBACK")   # clear stranded aborted tx (gotcha #5)
        except psycopg2.Error:
            pass
        self.assertEqual((0, 0), tuple(self.db.chain("devops")))
        # Untouched siblings keep their seeded open rows.
        self.assertEqual((0, 1), tuple(self.db.chain("sysadmin")))

    def test_epistemologist_boundary_trigger_present(self):
        self.db.apply_file(GRANT_PATHS["epistemologist"])
        _, _, _, _, _, _, _, _, trig = self.db.open_row("epistemologist")
        self.assertIn("out_of_domain_resolution", trig,
                      "the boundary that makes the authority ratifiable")


if __name__ == "__main__":
    unittest.main()
