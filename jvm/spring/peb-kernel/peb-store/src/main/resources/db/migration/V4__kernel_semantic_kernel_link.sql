-- V4: Link PEB transactions to the PostgreSQL Semantic Kernel.
--
-- Adds kernel_event_id and kernel_event_type columns to peb.transactions
-- so each PEB governance decision records its corresponding kernel
-- transition event.
--
-- The kernel schema is in the same database (nexus), separate schema.
-- It is created by the kernel subsystem's own bootstrapping, NOT by this
-- chain — so on fresh databases (CI throwaway DBs) the grants below are
-- skipped via the DO block. On databases where the kernel schema exists
-- (titanium production), re-granting is idempotent.
--
-- 2026-09-27 (PR #594): kernel columns were hand-applied to titanium and
-- Flyway was left disabled, so this migration never flew anywhere. It now
-- runs on fresh DBs as part of the peb-kernel fresh-database bootstrap
-- proven in the #594 CI build job.

-- ── Add kernel linkage columns to peb.transactions ──
ALTER TABLE peb.transactions
    ADD COLUMN IF NOT EXISTS kernel_event_id   UUID,
    ADD COLUMN IF NOT EXISTS kernel_event_type VARCHAR(32);

COMMENT ON COLUMN peb.transactions.kernel_event_id IS
    'FK to kernel.transition_event.event_id — the kernel event recorded
     for this PEB governance decision. Set by PebGovernanceEngine
     when it calls kernel.sys_transition().';

COMMENT ON COLUMN peb.transactions.kernel_event_type IS
    'The kernel event_type recorded (transition.requested,
     transition.committed, transition.rejected).';

-- Index for looking up PEB transactions by kernel event
CREATE INDEX IF NOT EXISTS idx_peb_transactions_kernel_event
    ON peb.transactions (kernel_event_id)
    WHERE kernel_event_id IS NOT NULL;

-- ── Grant kernel schema access to the application user ──
-- Conditional: the kernel schema is created by the kernel subsystem, not
-- this migration. Fresh throwaway DBs legitimately lack it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'kernel') THEN
        GRANT USAGE ON SCHEMA kernel TO pguser;
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA kernel TO pguser;
        GRANT SELECT ON ALL TABLES IN SCHEMA kernel TO pguser;
    ELSE
        RAISE NOTICE 'V4: kernel schema absent — grants skipped (fresh DB; peb columns still applied)';
    END IF;
END $$;
