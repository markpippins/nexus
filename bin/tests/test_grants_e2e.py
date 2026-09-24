"""E2E for the sql/grants events — engineer-iii (fresh-role) and supervisor.

House pattern (wr-conf companions): the REAL grant files apply to a
throwaway database built from a faithful minimal skeleton (roles_history +
bitemporal roles view + grant_is_applied + coordination_checkpoints
role-integrity trigger, shapes copied from titanium live 2026-09-24), then
the lifecycle is exercised end-to-end:

  engineer-iii (fresh-role variant, first of its kind):
    - fresh apply inserts the open snapshot (no close needed)
    - view carries the granted clone-of-engineer spec
    - replay is refused as GRANT-APPLIED (no redundant grant event)
    - re-grant path: different open spec -> close + re-insert, exact handoff
  supervisor (clone template already on main):
    - initial role_administration grant opens
    - replay is a quiet no-op (no second row)
  checkpoint trigger:
    - registered role passes the role-integrity check
    - unknown role is rejected (23503 semantics)

Creates and drops its own database (e2e_grants_<pid>). Requires a
PostgreSQL reachable via PG* env (defaults to localhost:5432/pguser).
"""

import os
import subprocess
import uuid

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
E3 = os.path.join(REPO, "sql", "grants", "engineer-iii-grant-v0.1.sql")
SUP = os.path.join(REPO, "sql", "grants", "supervisor-grant-v0.1.sql")

SKELETON = r"""
CREATE SCHEMA nebula;
CREATE TABLE nebula.roles_history (
  id uuid PRIMARY KEY,
  name text NOT NULL CHECK (name ~ '^[a-z0-9_-]+$'),
  display_name text NOT NULL,
  description text,
  owns_domains text[],
  can_greenlight boolean,
  can_create_questions boolean,
  can_create_agendas boolean,
  can_resolve_questions boolean,
  can_verify_work_requests boolean,
  max_open_questions integer,
  requires_approval_from text[],
  cron_enabled boolean,
  cron_expression text,
  cron_description text,
  escalates_to text[],
  escalation_triggers text[],
  level_filter_primary text,
  level_filter_allowed text,
  visibility_scope text[],
  created_at timestamptz,
  updated_at timestamptz,
  valid_from timestamptz,
  valid_until timestamptz,
  recorded_on_dt timestamptz,
  recorded_until_dt timestamptz
);
CREATE VIEW nebula.roles AS
  SELECT * FROM nebula.roles_history rh
  WHERE now() >= rh.recorded_on_dt AND now() < rh.recorded_until_dt
    AND now() >= rh.valid_from AND now() < rh.valid_until;
CREATE OR REPLACE FUNCTION nebula.grant_is_applied(p_role text, p_spec jsonb)
 RETURNS boolean LANGUAGE plpgsql STABLE AS $function$
DECLARE v_open int; v_open_spec jsonb;
BEGIN
    SELECT count(*) INTO v_open FROM nebula.roles_history r
     WHERE r.name = p_role
       AND r.valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND r.recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
    IF v_open = 0 THEN RETURN false; END IF;
    SELECT jsonb_build_object(
               'owns_domains',             to_jsonb(r.owns_domains),
               'can_greenlight',           to_jsonb(r.can_greenlight),
               'can_create_questions',     to_jsonb(r.can_create_questions),
               'can_create_agendas',       to_jsonb(r.can_create_agendas),
               'can_resolve_questions',    to_jsonb(r.can_resolve_questions),
               'can_verify_work_requests', to_jsonb(r.can_verify_work_requests),
               'max_open_questions',       to_jsonb(r.max_open_questions),
               'requires_approval_from',   to_jsonb(r.requires_approval_from),
               'escalates_to',             to_jsonb(r.escalates_to),
               'escalation_triggers',      to_jsonb(r.escalation_triggers),
               'visibility_scope',         to_jsonb(r.visibility_scope)
           ) INTO v_open_spec
      FROM nebula.roles_history r
     WHERE r.name = p_role
       AND r.valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND r.recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
    RETURN v_open_spec = p_spec;
END;
$function$;
CREATE TABLE nebula.coordination_checkpoints (
  role text NOT NULL,
  item_kind text NOT NULL CHECK (item_kind = ANY (ARRAY['inbox','todo','discussions','issues','change-log'])),
  last_reviewed_at timestamptz NOT NULL,
  reviewed_by_model text,
  note text,
  updated_at timestamptz NOT NULL
);
CREATE OR REPLACE FUNCTION nebula.tg_coordination_checkpoints_role_fk()
 RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM nebula.roles WHERE name = NEW.role) THEN
        RAISE EXCEPTION 'role-integrity: no role % in nebula.roles', NEW.role
              USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END;
$fn$;
CREATE TRIGGER coordination_checkpoints_role_fk BEFORE INSERT OR UPDATE OF role
  ON nebula.coordination_checkpoints FOR EACH ROW
  EXECUTE FUNCTION nebula.tg_coordination_checkpoints_role_fk();
"""

ENV = {**os.environ,
       "PGHOST": os.environ.get("PGHOST", "localhost"),
       "PGPORT": os.environ.get("PGPORT", "5432"),
       "PGUSER": os.environ.get("PGUSER", "pguser")}


def psql(db, sql, expect_fail=False):
    r = subprocess.run(["psql", "-d", db, "-v", "ON_ERROR_STOP=1", "-Atc", sql],
                       env=ENV, capture_output=True, text=True, timeout=60)
    if expect_fail:
        assert r.returncode != 0, f"expected failure, got success: {r.stdout}"
        return r.stdout + r.stderr
    assert r.returncode == 0, f"psql failed: {r.stderr}"
    return r.stdout.strip()


def run_file(db, path):
    r = subprocess.run(["psql", "-d", db, "-v", "ON_ERROR_STOP=1", "-f", path],
                       env=ENV, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"grant apply failed: {r.stderr}"
    return r.stdout + r.stderr


@pytest.fixture(scope="module")
def e2edb():
    db = f"e2e_grants_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    assert subprocess.run(["createdb", db], env=ENV,
                          capture_output=True).returncode == 0, "cannot create throwaway DB"
    r = subprocess.run(["psql", "-d", db, "-v", "ON_ERROR_STOP=1", "-q"],
                       input=SKELETON, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, f"skeleton failed: {r.stderr}"
    yield db
    subprocess.run(["dropdb", db], env=ENV, capture_output=True)


def test_fresh_engineer_iii_grant_inserts_open_snapshot(e2edb):
    out = run_file(e2edb, E3)
    assert "fresh-role registration" in out
    row = psql(e2edb, "select can_verify_work_requests::text||'|'||owns_domains::text"
                      " from nebula.roles where name='engineer-iii';")
    assert row == "true|{implementation,build_verification}"


def run_file_raw(db, path):
    return subprocess.run(["psql", "-d", db, "-v", "ON_ERROR_STOP=1", "-f", path],
                          env=ENV, capture_output=True, text=True, timeout=60)


def test_replay_is_refused_as_grant_applied(e2edb):
    r = run_file_raw(e2edb, E3)
    assert r.returncode != 0 and "GRANT-APPLIED" in r.stderr
    n = psql(e2edb, "select count(*) from nebula.roles_history where name='engineer-iii';")
    assert n == "1"


def test_regrant_path_closes_and_reopens_with_exact_handoff(e2edb):
    psql(e2edb, "UPDATE nebula.roles_history SET can_greenlight = true"
                " WHERE name='engineer-iii' AND valid_until='9999-12-31 00:00:00+00'::timestamptz;")
    out = run_file(e2edb, E3)
    assert "re-opened" in out
    assert psql(e2edb, "select can_greenlight::text from nebula.roles where name='engineer-iii';") == "false"
    n = psql(e2edb, "select count(*)||'/'||(select count(*) from nebula.roles_history"
                    " c where c.name='engineer-iii' AND c.valid_until <> '9999-12-31 00:00:00+00'::timestamptz"
                    " AND c.valid_until = (SELECT o.valid_from FROM nebula.roles_history o"
                    " WHERE o.name='engineer-iii' AND o.valid_until='9999-12-31 00:00:00+00'::timestamptz))"
                    " from nebula.roles_history where name='engineer-iii';")
    assert n == "2/1"


def test_supervisor_grant_opens_then_replay_is_quiet_noop(e2edb):
    out = run_file(e2edb, SUP)
    assert "initial role_administration grant opened" in out
    assert psql(e2edb, "select owns_domains::text from nebula.roles where name='supervisor';") == "{role_administration}"
    out2 = run_file(e2edb, SUP)
    assert "already open" in out2
    assert psql(e2edb, "select count(*) from nebula.roles_history where name='supervisor';") == "1"


def test_checkpoint_trigger_accepts_registered_rejects_unknown(e2edb):
    psql(e2edb, "INSERT INTO nebula.coordination_checkpoints (role, item_kind,"
                " last_reviewed_at, updated_at) VALUES ('engineer-iii','inbox',now(),now());")
    err = psql(e2edb, "INSERT INTO nebula.coordination_checkpoints (role, item_kind,"
                      " last_reviewed_at, updated_at) VALUES ('ghost-role','inbox',now(),now());",
               expect_fail=True)
    assert "role-integrity" in err
