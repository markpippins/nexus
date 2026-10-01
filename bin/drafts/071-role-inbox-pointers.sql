-- 071 — durable inbox pointers (Postgres canonical, Redis demoted to cache)
--
-- Architect defect record db3992b2 (high, system-wide, confirmed): all ten role inbox
-- pointers were destroyed by a Redis restart at 2026-10-01T08:16:37Z. Pointers were plain
-- SETs with no TTL against a Redis running `appendonly no`, so the restart restored only
-- the last RDB snapshot. Impact was silent re-delivery of every role's full history,
-- truncated at the limit cap so the tail was invisible — the "silent truncation read as
-- certainty" failure ruled against in 26560466.
--
-- WHY THIS IS IN bin/drafts AND NOT migrations/
-- migrations/ is applied AUTOMATICALLY at nebula-srv startup whenever NEBULA_MIGRATE_TARGET
-- is set, and it is currently set to the LIVE database. Adding a numbered file there would
-- change live on the next restart with no DBA sign-off. Standing doctrine is that the DBA
-- owns DDL; this file is the DBA's to review, place, and apply.
--
-- Apply as migration 071 once approved (ledger is currently at 70).

CREATE TABLE IF NOT EXISTS nebula.role_inbox_pointers (
    -- Role name. Deliberately the same vocabulary as the Redis key it replaces
    -- (`inbox:pointer:<role>`) so a cache-only pointer can be backfilled by role alone.
    role        text PRIMARY KEY,

    -- The delivery watermark: the last-seen instant, as a timestamptz rather than the
    -- free-text ISO string Redis held. A real timestamp type is what makes it queryable and
    -- makes a malformed value impossible, not merely unlikely.
    pointer     timestamptz NOT NULL,

    -- Defect impact #3 was "no audit trail for watermark state, so the loss cannot be dated
    -- per role". This column is the minimum fix: it dates the most recent write per role,
    -- which is what makes a future loss diagnosable rather than merely bounded.
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE nebula.role_inbox_pointers IS
    'Durable per-role inbox delivery watermarks. Canonical; Redis is cache only. '
    'Defect db3992b2: pointers were Redis-resident and were lost to a restart on 2026-10-01.';

COMMENT ON COLUMN nebula.role_inbox_pointers.role IS
    'Role name, matching the inbox:pointer:<role> key it replaces.';
COMMENT ON COLUMN nebula.role_inbox_pointers.pointer IS
    'Last-seen instant for this role. NULL is never stored; absence of a row means no pointer.';
COMMENT ON COLUMN nebula.role_inbox_pointers.updated_at IS
    'Last write time; dates the loss if one ever occurs again.';

-- Targeted index for "which roles have not advanced recently" — the query that would have
-- made the 2026-10-01 loss visible per role instead of only bounded by a restart time.
CREATE INDEX IF NOT EXISTS idx_role_inbox_pointers_updated_at
    ON nebula.role_inbox_pointers (updated_at);

-- Deliberately NOT append-only: a watermark is advanced in place by design, unlike
-- nebula.executions which is evidence and must never be mutated. One row per role is the
-- whole point — a watermark has no history worth the write amplification.
--
-- No auto-insert: a role with no row has genuinely never established a watermark, and
-- inventing one at DEFAULT now() would mark unseen records as seen — converting a detected
-- loss into a permanent one, which architect db3992b2 explicitly forbids ("Recovery: Pointers
-- must NOT be re-anchored to now").
