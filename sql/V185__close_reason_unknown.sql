-- V185 (DBA): duality.session_watches.closed_reason — add explicit
-- 'unknown' close code (inspector G3, discussions post 5d45f921).
-- ============================================================================
-- Problem (G3): conversation_coordinator maps the legacy aggregate
-- OUTCOME_CLOSED_LEASE (any lease closure without release granularity —
-- revoked, exhausted, or expired equally) to 'lease_expired'. An
-- exhausted-but-untagged legacy lease is recorded as time-expired in a
-- governance column: a false specific claim where the true statement is
-- "closed by lease end, granularity not recorded".
--
-- Grounding (live, 2026-09-19): session_watches carries NO release_reason
-- column — the evidence needed to reclassify legacy rows post-hoc was never
-- persisted. Correct response is an explicit 'unknown' value (absence as
-- data), NOT a conservative guess and NOT a data rewrite we cannot justify.
--
-- Data posture: zero rows currently hold closed_reason (verified live), so
-- the remap is preventive. No backfill UPDATE is included — with no
-- persisted evidence, rewriting nothing is the honest move; any future
-- evidence-bearing reclassification belongs in its own, separately
-- reviewed migration.
--
-- Vocabulary: closed_reason gains 'unknown'. All existing values unchanged;
-- nothing in the live CHECK is removed, so no row can become invalid.
--
-- Idempotent: constraint rebuild guarded by a definition probe.
-- ============================================================================

DO $$
BEGIN
    -- Guard: only rebuild when the old inline CHECK is still present.
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'duality.session_watches'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%lease_expired%'
          AND pg_get_constraintdef(oid) NOT LIKE '%unknown%'
    ) THEN
        -- 1. Drop the old inline CHECK (name from V130's ADD COLUMN).
        ALTER TABLE duality.session_watches
            DROP CONSTRAINT IF EXISTS session_watches_closed_reason_check;

        -- 2. Re-add the full vocabulary: previous values plus 'unknown'.
        ALTER TABLE duality.session_watches
            ADD CONSTRAINT session_watches_closed_reason_check
            CHECK (closed_reason IN (
                'lease_revoked', 'lease_exhausted', 'lease_expired',
                'turns', 'agent', 'idle', 'natural', 'unknown'));
    END IF;
END;
$$;
