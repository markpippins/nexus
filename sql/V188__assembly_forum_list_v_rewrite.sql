-- =============================================================================
--  V188 — assembly-srv: forum_list_v count-subquery rewrite + workhorse indexes
--
--  Incident 2026-09-20 (records 60f90514 / e2a33134, "Assembly issue"):
--  GET /api/forums took 6.3-8.0s for a 7KB payload, intermittently blowing
--  past client timeouts — agents were reading "Assembly is broken" off a pure
--  query-shape pathology. EXPLAIN showed two uncorrelated count SubPlans
--  (a full scan of assembly.posts and one of assembly.comments, 79K rows)
--  executed once per forum row (29x). The access paths underneath were also
--  missing entirely: assembly.posts and assembly.comments carry ONLY their
--  pkey indexes, so even a correlated rewrite would seq-scan.
--
--  Fix (semantic no-op, shape-preserving for consumers):
--    1. Rewrite forum_list_v counts as grouped left-join aggregates — one
--       pass over posts, one over comments. Mirrors the fix already applied
--       to thread_list_v in the same views file ("the correlated version ran
--       each subquery once per row — 2052x for transcripts").
--    2. Index posts(forum_uuid) and comments(post_id) — the workhorse access
--       paths for every per-forum/thread listing in assembly-srv.
--    3. Partial btree on posts(forum_uuid, rating) for current rows (rating
--       feeds thread status everywhere; no per-forum path exists today).
--
--  Idempotent: DROP+CREATE for the view, CREATE INDEX IF NOT EXISTS.
--  The plain DROP is deliberate: live has zero dependents on forum_list_v
--  (verified 2026-09-20); if a dependent appears, migration fails loudly
--  rather than CASCADE-destroying it silently.
-- =============================================================================

BEGIN;

-- ── 1. Workhorse indexes ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_posts_forum_uuid
    ON assembly.posts (forum_uuid);
CREATE INDEX IF NOT EXISTS idx_comments_post_id
    ON assembly.comments (post_id);
CREATE INDEX IF NOT EXISTS idx_posts_forum_uuid_current
    ON assembly.posts (forum_uuid, rating)
    WHERE expiration_dt = 'infinity'::timestamptz;

ANALYZE assembly.posts;
ANALYZE assembly.comments;

-- ── 2. forum_list_v rewrite (grouped aggregates; same columns and order) ────
DROP VIEW IF EXISTS assembly.forum_list_v;

CREATE VIEW assembly.forum_list_v AS
SELECT
    f.id,
    f.name,
    f.slug,
    f.description,
    f.sort_order,
    f.expiration_dt,
    COALESCE(pc.thread_count, 0)  AS thread_count,
    COALESCE(cc.comment_count, 0) AS comment_count
FROM assembly.forums f
LEFT JOIN (
    SELECT p.forum_uuid, COUNT(*) AS thread_count
    FROM assembly.posts p
    GROUP BY p.forum_uuid
) pc ON pc.forum_uuid = f.id
LEFT JOIN (
    SELECT p.forum_uuid, COUNT(*) AS comment_count
    FROM assembly.comments c
    JOIN assembly.posts p ON p.id = c.post_id
    GROUP BY p.forum_uuid
) cc ON cc.forum_uuid = f.id
WHERE f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now()
ORDER BY COALESCE(f.sort_order, 0) ASC, f.name ASC;

COMMENT ON VIEW assembly.forum_list_v IS
'Forum listing with thread/comment counts (V188: grouped-aggregate rewrite of the per-row count subqueries — one pass over posts/comments instead of two full scans per forum row).';

-- ── 3. Post-apply verification (hard gate, V081 house style) ────────────────
DO $$
DECLARE
    v_view_rows integer;
    v_runtime   interval;
    v_t0        timestamptz;
BEGIN
    SELECT count(*) INTO v_view_rows FROM assembly.forum_list_v;
    IF v_view_rows = 0 THEN
        RAISE EXCEPTION 'V188-VERIFY-001: forum_list_v returned zero rows after rewrite'
            USING ERRCODE = 'P0001';
    END IF;

    SELECT clock_timestamp() INTO v_t0;
    PERFORM id, slug, thread_count, comment_count FROM assembly.forum_list_v
    ORDER BY (COALESCE(sort_order, 0)), name;
    v_runtime := clock_timestamp() - v_t0;

    RAISE NOTICE '✅ V188 applied — forum_list_v rewritten (rows: %), list query runtime: %',
        v_view_rows, v_runtime;
END $$;

COMMIT;
