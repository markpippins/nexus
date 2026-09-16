-- =============================================================================
-- V168 (DBA): extend role_leases channel vocabulary with 'ui-fleet'.
--
-- Motivation: standing operator-role lease for UI-origin /chat calls — the
-- enforce-flip prerequisite from the lease-check soak review (agent record
-- 6a9ed591, discussions thread 65fe85a8). Interactive agent sessions are
-- leased by the boot shim on the 'interactive' channel; the UI fleet is a
-- distinct lease consumer class and needs its own channel so the house rule
-- "one ACTIVE lease per (role, channel)" (D-2026-08-16-007 R2) stays intact:
-- an operator@ui-fleet standing lease never collides with, and its renewal
-- never extends, an agent's operator@interactive lease.
--
-- Additive only: extends the channel CHECK ARRAY with one value. No rows are
-- touched, no existing channel is removed, no service restart required.
--
-- Idempotency: the constraint is dropped and recreated only when 'ui-fleet'
-- is not already admitted (guard on the constraint definition text).
-- =============================================================================

BEGIN;

DO $$
BEGIN
    IF NOT (
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'tackle.role_leases'::regclass
          AND conname = 'role_leases_channel_check'
    ) LIKE '%ui-fleet%' THEN
        ALTER TABLE tackle.role_leases DROP CONSTRAINT role_leases_channel_check;
        ALTER TABLE tackle.role_leases ADD CONSTRAINT role_leases_channel_check
            CHECK ((channel = ANY (ARRAY['interactive'::text, 'opencode'::text,
                                         'ollama'::text, 'ui-fleet'::text,
                                         'unknown'::text])));
        RAISE NOTICE 'V168: role_leases_channel_check extended with ui-fleet';
    ELSE
        RAISE NOTICE 'V168: ui-fleet already admitted — no change';
    END IF;
END;
$$;

COMMIT;
