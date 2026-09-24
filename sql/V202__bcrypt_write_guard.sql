-- =============================================================================
-- V202 (DBA): bcrypt write-guard — the enforcement path that keeps V191 landed.
-- =============================================================================
-- History: V191 (2026-09-20, issue 59bcd3da) staged the bcrypt backfill +
-- born-clean CHECKs as "applies on explicit operator go" — and the go never
-- reached the titanium nexus DB: DBA archive forensics (2026-09-24) proved
-- zero bcrypt rows existed on any users surface in any backup back to 09-18;
-- the live census was 100% bootstrap plaintext literals (changeme x16, agent
-- x8, ...). Never-landed, not regressed. This migration is the operator go,
-- shipped inside the migration chain so born-clean and live converge.
--
-- What it does:
--   1. Re-applies the V191 backfill verbatim (idempotent $2% guard, never
--      destructive) — the live-apply vehicle.
--   2. Re-asserts the V191 users_password_bcrypt_check CHECKs (idempotent).
--   3. Installs BEFORE INSERT/UPDATE write-guard triggers on both surfaces:
--      any plaintext password is bcrypt-hashed on the way in, so the two
--      known plaintext writers (assembly-srv :3107 createUser route, adonis
--      control-edge `changeme` default) keep functioning while the CHECK
--      makes raw plaintext writes impossible. Defense-in-depth: trigger
--      normalizes, CHECK backstops, writers never see a 500.
--
-- Idempotent: backfill is a no-op once everything is bcrypt; triggers use
-- DROP IF EXISTS + CREATE; CHECKs use the V191 DO guard. Never destructive:
-- existing hashes are never re-hashed (WHERE guard + trigger pass-through).
--
-- Failure semantics are fail-closed: if pgcrypto is ever dropped, the
-- trigger's crypt() call errors and the write is REJECTED — the guard can
-- never silently degrade back to storing plaintext.
--
-- Post-apply verification (read-only):
--   SELECT count(*) FROM assembly.users WHERE password NOT LIKE '$2%';  -- 0
--   SELECT count(*) FROM gateway.users  WHERE password NOT LIKE '$2%';  -- 0
--   -- trigger self-hash proof (rollback afterwards):
--   BEGIN; INSERT INTO assembly.users (alias, email, identifier, password)
--          VALUES ('probe','probe@localhost','pw','plaintextpw') RETURNING password;
--   -- expect a $2… 60-char hash; ROLLBACK;
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Preflight: both surfaces must exist (foreign topology -> refuse, don't guess).
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('assembly.users') IS NULL THEN
        RAISE EXCEPTION 'V202 PREFLIGHT FAIL: assembly.users does not exist';
    END IF;
    IF to_regclass('gateway.users') IS NULL THEN
        RAISE EXCEPTION 'V202 PREFLIGHT FAIL: gateway.users does not exist';
    END IF;
END $$;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- Step 1 — the V191 backfill, verbatim (idempotent, never destructive).
-- -----------------------------------------------------------------------------

-- assembly.users — the live auth surface (user-access-service validateUser).
UPDATE assembly.users
   SET password = crypt(password, gen_salt('bf', 10)),
       updated_at = now()
 WHERE password NOT LIKE '$2%';

-- gateway.users — write-orphaned surface, closed for class-completeness.
UPDATE gateway.users
   SET password = crypt(password, gen_salt('bf', 10)),
       updated_at = now()
 WHERE password NOT LIKE '$2%';

-- -----------------------------------------------------------------------------
-- Step 2 — the V191 born-clean CHECKs, re-asserted idempotently. Accepts
-- bcrypt ($2a/$2b/$2y, 60 chars) or '' (the JPA constructor escape V191
-- ratified). This is what makes a plaintext writer impossible even if the
-- triggers are somehow removed.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'users_password_bcrypt_check'
                     AND conrelid = 'assembly.users'::regclass) THEN
        ALTER TABLE assembly.users
            ADD CONSTRAINT users_password_bcrypt_check
            CHECK (password LIKE '$2%' AND length(password) = 60 OR password = '');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'users_password_bcrypt_check'
                     AND conrelid = 'gateway.users'::regclass) THEN
        ALTER TABLE gateway.users
            ADD CONSTRAINT users_password_bcrypt_check
            CHECK (password LIKE '$2%' AND length(password) = 60 OR password = '');
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- Step 3 — the write-guard function + triggers. Plaintext in -> bcrypt out.
-- A malformed '$2'-prefixed value that is not a valid hash is passed through
-- untouched so the CHECK (length = 60) still rejects it loudly — the trigger
-- normalizes honest plaintext; it does not launder garbage.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.bcrypt_write_guard() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.password IS NOT NULL
       AND NEW.password <> ''
       AND NEW.password NOT LIKE '$2%' THEN
        NEW.password := crypt(NEW.password, gen_salt('bf', 10));
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_bcrypt_write_guard ON assembly.users;
CREATE TRIGGER trg_bcrypt_write_guard
    BEFORE INSERT OR UPDATE OF password ON assembly.users
    FOR EACH ROW EXECUTE FUNCTION public.bcrypt_write_guard();

DROP TRIGGER IF EXISTS trg_bcrypt_write_guard ON gateway.users;
CREATE TRIGGER trg_bcrypt_write_guard
    BEFORE INSERT OR UPDATE OF password ON gateway.users
    FOR EACH ROW EXECUTE FUNCTION public.bcrypt_write_guard();

COMMIT;

-- =============================================================================
-- Verification queries (manual, read-only) — see file header.
-- =============================================================================
