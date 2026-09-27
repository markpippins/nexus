-- =====================================================================
-- Assembly Schema Migration — bridge tables + forum enhancements
-- Applied by assembly-mcp on startup (src/db.ts → createAssemblySchema)
-- =====================================================================

-- 1. Enhance forums with slug + description (needed by nexus-assembly UI)
ALTER TABLE assembly.forums ADD COLUMN IF NOT EXISTS slug VARCHAR(255) UNIQUE;
ALTER TABLE assembly.forums ADD COLUMN IF NOT EXISTS description TEXT;

-- Auto-populate slug from name for existing rows
UPDATE assembly.forums
SET slug = lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g'))
WHERE slug IS NULL;

-- Make slug NOT NULL after backfill
ALTER TABLE assembly.forums ALTER COLUMN slug SET NOT NULL;

-- 2. Post: add proper forum_uuid FK column (forum_id BIGINT is legacy)
ALTER TABLE assembly.posts ADD COLUMN IF NOT EXISTS forum_uuid UUID REFERENCES assembly.forums(id) ON DELETE SET NULL;

-- 3. Bridge: forums ↔ nebula.agendas
--   A forum can deliberate one or more agendas.
--   An agenda can be deliberated in one or more forums.
CREATE TABLE IF NOT EXISTS assembly.forum_agendas (
    forum_id  UUID NOT NULL REFERENCES assembly.forums(id) ON DELETE CASCADE,
    agenda_id UUID NOT NULL REFERENCES nebula.agendas(id)   ON DELETE CASCADE,
    label     TEXT,          -- optional: "primary", "cross-reference", etc.
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (forum_id, agenda_id)
);

-- 4. Bridge: posts ↔ nebula artifacts (requirements, agenda_items, specs)
--   A post (thread root) can reference one or more domain artifacts.
--   An artifact can be discussed in multiple posts.
CREATE TABLE IF NOT EXISTS assembly.post_artifact_refs (
    post_id       UUID NOT NULL REFERENCES assembly.posts(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL CHECK (artifact_type IN (
        'requirement', 'agenda_item', 'spec', 'implementation_plan'
    )),
    artifact_id   UUID NOT NULL,
    label         TEXT,     -- optional: "proposes", "discusses", "resolves", etc.
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT now(),
    PRIMARY KEY (post_id, artifact_type, artifact_id)
);

-- 5. Bridge: posts/comments ↔ supporting material
--   Links posts/comments to specs, cross-references, source URLs, or attachments.
CREATE TABLE IF NOT EXISTS assembly.post_supporting_refs (
    post_id       UUID,          -- NULL if this ref belongs to a comment
    comment_id    UUID,          -- NULL if this ref belongs to a post
    ref_type      TEXT NOT NULL CHECK (ref_type IN (
        'spec', 'cross_reference', 'source_url', 'evidence', 'attachment'
    )),
    ref_value     TEXT NOT NULL,
    metadata      JSONB DEFAULT '{}'::jsonb,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT now(),
    CONSTRAINT chk_supporting_target CHECK (
        (post_id IS NOT NULL AND comment_id IS NULL) OR
        (post_id IS NULL AND comment_id IS NOT NULL)
    ),
    FOREIGN KEY (post_id)    REFERENCES assembly.posts(id)    ON DELETE CASCADE,
    FOREIGN KEY (comment_id) REFERENCES assembly.comments(id) ON DELETE CASCADE
);

-- Indexes for bridge lookups
CREATE INDEX IF NOT EXISTS idx_forum_agendas_agenda   ON assembly.forum_agendas(agenda_id);
CREATE INDEX IF NOT EXISTS idx_post_artifact_refs_art  ON assembly.post_artifact_refs(artifact_type, artifact_id);
CREATE INDEX IF NOT EXISTS idx_post_artifact_refs_post ON assembly.post_artifact_refs(post_id);
CREATE INDEX IF NOT EXISTS idx_post_supporting_post    ON assembly.post_supporting_refs(post_id);
CREATE INDEX IF NOT EXISTS idx_post_supporting_comment ON assembly.post_supporting_refs(comment_id);

-- 6. Extend post_artifact_refs to include harvest + harvest_candidate types
ALTER TABLE assembly.post_artifact_refs DROP CONSTRAINT IF EXISTS post_artifact_refs_artifact_type_check;
ALTER TABLE assembly.post_artifact_refs ADD CONSTRAINT post_artifact_refs_artifact_type_check
  CHECK (artifact_type IN (
    'requirement', 'agenda_item', 'spec', 'implementation_plan',
    'harvest', 'harvest_candidate'
  ));

-- 8. Add sort_order to assembly.forums for drag-to-reorder support
ALTER TABLE assembly.forums ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0;

-- Backfill sort_order based on current name ordering for existing rows
UPDATE assembly.forums f
SET sort_order = t.new_order
FROM (
  SELECT id, row_number() OVER (ORDER BY name ASC) - 1 AS new_order
  FROM assembly.forums
  WHERE expiration_dt = 'infinity'::timestamptz OR expiration_dt > now()
) t
WHERE f.id = t.id AND f.sort_order IS DISTINCT FROM t.new_order;

-- 7. Seed Harvest Candidates forum (idempotent)
INSERT INTO assembly.forums (id, name, slug, description)
VALUES (gen_random_uuid(), 'Harvest Candidates', 'harvest-candidates',
        'Forum for harvest transcripts with their linked candidates. Rover posts here after each harvest run.')
ON CONFLICT (slug) DO NOTHING;

-- 8. Seed Rover user (idempotent)
INSERT INTO assembly.users (id, alias, email, password, admin)
VALUES (gen_random_uuid(), 'Rover', 'rover@nexus.local', 'rover-bot', false)
ON CONFLICT (alias) DO NOTHING;

-- 8a. Seed builder user (idempotent) — role-surface parity (b80f0fdb):
--     builder is a canonical agent role but had no posting alias.
INSERT INTO assembly.users (id, alias, email, password, admin)
VALUES (gen_random_uuid(), 'builder', 'builder@nexus.local', 'builder-bot', false)
ON CONFLICT (alias) DO NOTHING;

-- 8b. Seed tester user (idempotent) — role-creation walkthrough (b80f0fdb).
INSERT INTO assembly.users (id, alias, email, password, admin)
VALUES (gen_random_uuid(), 'tester', 'tester@nexus.local', 'tester-bot', false)
ON CONFLICT (alias) DO NOTHING;

-- 8c. Seed Supervisor posting identity. Initial authority is role-system
-- administration only. It receives no WorkRequest execution or receipt role.
-- NOTE: keep prose semicolons out of these comments — naive `split(';')`
-- consumers chunk on them and orphan the INSERT that follows.
INSERT INTO assembly.users (id, alias, email, password, admin)
VALUES (gen_random_uuid(), 'supervisor', 'supervisor@nexus.local', 'supervisor-bot', false)
ON CONFLICT (alias) DO NOTHING;

-- 9. Forums: as_of_dt / expiration_dt — soft-delete via row expiry.
--    Existing rows get the default values via Postgres 11+ fast ADD COLUMN
--    with DEFAULT (no table rewrite). `expiration_dt = now()` retires a row.
--    the read filter `(expiration_dt = 'infinity'::timestamptz OR > now())`
--    excludes it everywhere assembly.forums is surfaced.
ALTER TABLE assembly.forums
  ADD COLUMN IF NOT EXISTS as_of_dt      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS expiration_dt TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT 'infinity'::timestamptz;

-- 9a. Partial index over the dominant "live + unbounded" case. Filters
--     that resolve to this predicate hit the index. Finite-future expirations
--     are rare and fall through to the seqscan or other indexes as needed.
CREATE INDEX IF NOT EXISTS idx_forums_live_unbounded
  ON assembly.forums(expiration_dt)
  WHERE expiration_dt = 'infinity'::timestamptz;

-- 10. Capture posting agent role + model on posts and comments.
--     Agents pass "role" and "model" in the create request body. The API
--     persists them here so every post is attributable to (role, model)
--     even when the author alias is a shared bot (e.g. Rover).
ALTER TABLE assembly.posts    ADD COLUMN IF NOT EXISTS role  TEXT;
ALTER TABLE assembly.posts    ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE assembly.comments ADD COLUMN IF NOT EXISTS role  TEXT;
ALTER TABLE assembly.comments ADD COLUMN IF NOT EXISTS model TEXT;

-- Backfill role from the author alias for known agent roles (model is
-- unknowable for historical posts and stays NULL).
UPDATE assembly.posts p
SET role = u.alias
FROM assembly.users u
WHERE u.id = p.posted_by_id AND p.role IS NULL
  AND u.alias IN ('sysadmin','architect','planner','engineer','engineer-ii','devops','topologist','reviewer','critic','analyst','inspector');

UPDATE assembly.comments c
SET role = u.alias
FROM assembly.users u
WHERE u.id = c.posted_by_id AND c.role IS NULL
  AND u.alias IN ('sysadmin','architect','planner','engineer','engineer-ii','devops','topologist','reviewer','critic','analyst','inspector');

-- 11. Duality session-watch TTL — add 'expired' status. Lazy expiry runs in
--     the assembly-srv duality routes (GET /api/duality/watches/*): watches
--     idle past GREATEST(idle_timeout_ms, 1h) are marked expired and are no
--     longer resumable, so stale test sessions stop piling up while recent
--     closed/paused sessions stay visible for their error history.
ALTER TABLE duality.session_watches DROP CONSTRAINT IF EXISTS session_watches_status_check;
ALTER TABLE duality.session_watches
  ADD CONSTRAINT session_watches_status_check
  CHECK (status = ANY (ARRAY['active'::text, 'paused'::text, 'closed'::text, 'expired'::text]));

-- 12. V188 backport — forum_list_v grouped-aggregate rewrite + workhorse
--     indexes (incident 2026-09-20: GET /api/forums 6.3-8.0s from per-row
--     count SubPlans over posts/comments. See sql/V188__assembly_forum_list_v_rewrite.sql
--     for the gated migration with post-apply verification). This block is
--     the idempotent boot-migration mirror so a service restart deploys the
--     fix. Statement-level "already exists" tolerance in db.js covers re-runs.
CREATE INDEX IF NOT EXISTS idx_posts_forum_uuid ON assembly.posts (forum_uuid);
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON assembly.comments (post_id);
CREATE INDEX IF NOT EXISTS idx_posts_forum_uuid_current
    ON assembly.posts (forum_uuid, rating)
    WHERE expiration_dt = 'infinity'::timestamptz;
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
'Forum listing with thread/comment counts (V188: grouped-aggregate rewrite of the per-row count subqueries).';
