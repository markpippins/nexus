-- Migration 069: Forensic stamping for nebula.schema_version (DBA lane)
-- Part of DBA proposal 5ab78e30 (§2.3) — the DBA tier of the migration-runner
-- target-identity guard responding to SEV3 9a70c8c5.
--
-- What it does:
--   1. Adds nebula.schema_version_audit — one row per ledger stamp, capturing
--      WHO (db user), FROM WHERE (client address), and HOW (application_name)
--      at the moment of every future stamp. Today, attribution of a stamp is
--      reconstructed from timestamps (see 3aa390da: the 056/057 SEV3 forensics);
--      after this migration it is a query.
--   2. Binds an AFTER INSERT trigger on nebula.schema_version. Every runner
--      (startup migrate.ts, hand-applied DBA work) stamps through INSERT, so
--      the audit captures all stamping paths with no runner changes.
--   3. Self-stamps the ledger (043 convention), which simultaneously proves
--      the trigger works: the migration's own stamp must appear in the audit.
--
-- Depends on: nothing (schema_version exists since 043).
-- Replay-safe: CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE / DROP IF EXISTS.
-- Order hazard (encoded in APPLY-068.sh): MUST NOT be applied while ledger < 68.
--   The runner is forward-only from MAX(version) — stamping 69 while 068 is
--   pending would make the next boot SKIP the 067 file forever (the same
--   dead-number trap ruled on in 3aa390da, in reverse).
--
-- WARNING — this file is restored as a DRAFT after the repo-wide read strip.
-- It has NOT been verified readable in full after that strip, and it is NOT
-- executable from bin/drafts/ until it passes the same gates as everything else
-- here (DBA sign-off, ordering check, production-target confirmation). Do not
-- run it from here. It is retained so the DDL can be rebuilt from its container,
-- not invented from memory.

BEGIN;

-- ── 1. Audit table ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nebula.schema_version_audit (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version     INTEGER     NOT NULL,
  description TEXT        NOT NULL,
  db_user     TEXT        NOT NULL DEFAULT current_user,
  client_addr TEXT                 DEFAULT host(inet_client_addr()),
  app_name    TEXT                 DEFAULT current_setting('application_name', true),
  stamped_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE nebula.schema_version_audit IS
  'Forensic ledger of nebula.schema_version stamps (migration 069, DBA proposal 5ab78e30): one row per INSERT into the forward ledger, capturing db user, client address, and application_name at stamp time. Append-only by trigger; rows are never updated or deleted.';

-- ── 2. Trigger function ────────────────────────────────────────
CREATE OR REPLACE FUNCTION nebula.fn_schema_version_audit_ins()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO nebula.schema_version_audit (version, description)
  VALUES (NEW.version, NEW.description);
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION nebula.fn_schema_version_audit_ins() IS
  'Migration 069: mirrors every nebula.schema_version INSERT into schema_version_audit with caller identity (current_user, inet_client_addr, application_name).';

-- ── 3. Bind trigger (replace-safe) ─────────────────────────────
DROP TRIGGER IF EXISTS trg_schema_version_audit ON nebula.schema_version;
CREATE TRIGGER trg_schema_version_audit
AFTER INSERT ON nebula.schema_version
FOR EACH ROW EXECUTE FUNCTION nebula.fn_schema_version_audit_ins();

-- ── 4. Self-stamp (043 convention; proves the trigger live) ────
INSERT INTO nebula.schema_version (version, description)
VALUES (69, 'schema_version forensic stamping: audit table + trigger (DBA proposal 5ab78e30)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
