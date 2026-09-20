-- =============================================================================
-- V191 (DBA): bcrypt-at-rest for the Spring auth surfaces — assembly.users
-- (the LIVE auth surface: user-access-service validateUser) and gateway.users
-- (write-orphaned, still plaintext). Security remediation items 1+2 from
-- issue 59bcd3da, verified live 2026-09-20:
--
--   * assembly.users: 8+ rows with PLAINTEXT passwords (admin included);
--     UserAccessService.validateUser authenticates with plaintext equals —
--     converted to BCryptPasswordEncoder.matches() in the same PR
--   * gateway.users: 2 plaintext rows (admin/testuser); the owning module
--     (broker-gateway JPA ddl-auto=update) writes but nothing authenticates
--     against it — backfilled identically so the class is closed everywhere
--   * user-creation-service AdminUserSeeder (committed admin.password=admin
--     in broker-gateway application.properties) plaintext-overwrites on
--     every boot — REMOVED in the same PR; this migration backfills, so
--     together the plaintext state never returns
--
-- Staged inert: applies on explicit operator go, house doctrine. Idempotent:
-- the backfill is a no-op once every password is already bcrypt. Never
-- destructive: existing bcrypt hashes are left untouched (WHERE guard).
--
-- Verification (after Java deploy):
--   crypt('admin', <stored-hash>) = <stored-hash>   -- pgcrypto round-trip
--   length(password) = 60 AND password LIKE '$2%'
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Preflight: both surfaces must exist (foreign topology -> refuse, don't guess).
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('assembly.users') IS NULL THEN
        RAISE EXCEPTION 'V191 PREFLIGHT FAIL: assembly.users does not exist';
    END IF;
    IF to_regclass('gateway.users') IS NULL THEN
        RAISE EXCEPTION 'V191 PREFLIGHT FAIL: gateway.users does not exist';
    END IF;
END $$;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- Backfill: bcrypt (bf, cost 10) every password not already a bcrypt hash.
-- The $2% guard makes this idempotent and never destructive — re-apply is a
-- zero-row no-op, and pre-existing hashes (if any) are never re-hashed.
-- -----------------------------------------------------------------------------

-- assembly.users — the live auth surface (validateUser -> BCrypt matches()).
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
-- Born-clean going forward: a format CHECK per surface. The check accepts
-- bcrypt hashes only ($2a/$2b/$2y, 60 chars) OR the empty string (the same
-- escape the role CHECKs use — JPA constructs entities with '' before
-- setIdentifier runs, and validateUser already rejects empty credentials).
-- This CHECK is what makes the seeder class impossible to reintroduce: any
-- writer that tries to store plaintext is rejected by the database itself.
-- -----------------------------------------------------------------------------
-- Idempotent add: PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS — the DO
-- guard makes re-apply a true no-op (the backfill above already is).
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

COMMIT;

-- -----------------------------------------------------------------------------
-- Post-apply verification (manual, read-only):
--   SELECT count(*) FROM assembly.users WHERE password NOT LIKE '$2%';  -- 0
--   SELECT count(*) FROM gateway.users  WHERE password NOT LIKE '$2%';  -- 0
--   -- round-trip proof (substitute a real hash):
--   SELECT crypt('admin', '<hash>') = '<hash>';
--   -- and the Java side: login-service login with the pre-migration
--   -- password must succeed via BCryptPasswordEncoder.matches().
-- =============================================================================
