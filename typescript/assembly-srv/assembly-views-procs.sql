-- ============================================================================
-- Assembly Views & Stored Procedures
-- Replaces inline SQL in assembly-srv route files.
-- Views expose full row sets; JS filters with WHERE clauses for parameterization.
-- Stored procedures handle multi-step write operations atomically.
-- ============================================================================

-- ── VIEWS ──────────────────────────────────────────────────────────────────

-- 1. Forum list with thread/comment counts
--     V188 (2026-09-20): grouped-aggregate rewrite of the original per-row
--     count subqueries — those compiled to two UNCORRELATED full-scan SubPlans
--     over posts/comments executed once per forum row (29x), making
--     GET /api/forums take 6.3-8.0s. Same columns/order; see
--     sql/V188__assembly_forum_list_v_rewrite.sql for the gated migration.
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

COMMENT ON VIEW assembly.forum_list_v IS 'Forum listing with thread/comment counts (V188 grouped-aggregate rewrite), replaces forums.js:11 inline query';

-- 2. Thread list (per-forum, parameterized via WHERE clause in JS)
-- Aggregated in ONE pass over comments (GROUP BY + array_agg) instead of 3
-- correlated subqueries per post — the correlated version ran each subquery
-- once per row (2052x for transcripts), making every list request ~1.8s even
-- when paginated. Column names/shapes are unchanged for consumers.
CREATE OR REPLACE VIEW assembly.thread_list_v AS
SELECT
    p.id AS post_id,
    p.title,
    p.created AS post_created,
    p.text,
    p.role,
    p.model,
    p.expiration_dt,
    u.id AS user_id,
    u.alias,
    u.avatar_url,
    f.id AS forum_id,
    f.slug AS forum_slug,
    f.name AS forum_name,
    COALESCE(c.reply_count, 0) AS reply_count,
    c.last_reply_at,
    c.last_reply_user_alias,
    p.rating
FROM assembly.posts p
JOIN assembly.forums f ON f.id = p.forum_uuid AND (f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now())
JOIN assembly.users u ON u.id = p.posted_by_id
LEFT JOIN (
    SELECT
        cc.post_id,
        COUNT(*) AS reply_count,
        MAX(cc.created) AS last_reply_at,
        (array_agg(uu.alias ORDER BY cc.created DESC))[1] AS last_reply_user_alias
    FROM assembly.comments cc
    JOIN assembly.users uu ON uu.id = cc.posted_by_id
    GROUP BY cc.post_id
) c ON c.post_id = p.id
WHERE (p.expiration_dt = 'infinity'::timestamptz OR p.expiration_dt > now())
ORDER BY p.created DESC;

COMMENT ON VIEW assembly.thread_list_v IS 'Thread listing per forum, replaces forums.js:45 inline query. Filter by forum_slug in WHERE clause.';

-- 3. Feed posts with recursive comment counts
CREATE OR REPLACE VIEW assembly.feed_posts_v AS
SELECT
    p.id AS post_id,
    p.text,
    p.created,
    p.expiration_dt,
    u.id AS user_id,
    u.alias,
    u.avatar_url,
    f.id AS forum_id,
    f.slug AS forum_slug,
    f.name AS forum_name,
    (
        WITH RECURSIVE tree AS (
            SELECT id FROM assembly.comments WHERE post_id = p.id
            UNION ALL
            SELECT c.id FROM assembly.comments c
            JOIN tree t ON c.parent_id = t.id
        )
        SELECT COUNT(*) FROM tree
    ) AS comment_count
FROM assembly.posts p
JOIN assembly.users u ON u.id = p.posted_by_id
LEFT JOIN assembly.forums f ON f.id = p.forum_uuid AND (f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now())
WHERE (p.expiration_dt = 'infinity'::timestamptz OR p.expiration_dt > now())
ORDER BY p.created DESC;

COMMENT ON VIEW assembly.feed_posts_v IS 'Feed listing with recursive comment counts, replaces feed.js:9 inline query';

-- 4. User list
CREATE OR REPLACE VIEW assembly.user_list_v AS
SELECT
    id,
    alias,
    email,
    avatar_url,
    created_at
FROM assembly.users
ORDER BY alias ASC;

COMMENT ON VIEW assembly.user_list_v IS 'All users ordered by alias, replaces users.js:9 inline query';

-- 5. User by ID
CREATE OR REPLACE VIEW assembly.user_by_id_v AS
SELECT
    id,
    alias,
    email,
    avatar_url,
    created_at
FROM assembly.users;

COMMENT ON VIEW assembly.user_by_id_v IS 'User detail, filter by id in WHERE clause. Replaces users.js:31 inline query.';

-- 6. Forum agendas
CREATE OR REPLACE VIEW assembly.forum_agendas_v AS
SELECT
    forum_id,
    agenda_id,
    label,
    created_at,
    expiration_dt
FROM assembly.forum_agendas
WHERE (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now());

COMMENT ON VIEW assembly.forum_agendas_v IS 'Forum-agenda links, filter by forum_id. Replaces bridges.js:49 inline query.';

-- 7. Artifact refs per post
CREATE OR REPLACE VIEW assembly.artifact_refs_v AS
SELECT
    post_id,
    artifact_type,
    artifact_id,
    label,
    created_at,
    expiration_dt
FROM assembly.post_artifact_refs
WHERE (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now());

COMMENT ON VIEW assembly.artifact_refs_v IS 'Post-artifact refs, filter by post_id. Replaces bridges.js:100 inline query.';


-- ── STORED PROCEDURES ─────────────────────────────────────────────────────

-- 1. Create forum (auto-assigns sort_order to end of list)
CREATE OR REPLACE FUNCTION assembly.create_forum(
    p_name text,
    p_slug text,
    p_description text DEFAULT NULL
)
RETURNS TABLE(id uuid, name text, slug text, description text, sort_order integer)
LANGUAGE plpgsql
AS $$
DECLARE
    next_order integer;
BEGIN
    SELECT COALESCE(MAX(f.sort_order), -1) + 1 INTO next_order
    FROM assembly.forums f
    WHERE f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now();

    RETURN QUERY
    INSERT INTO assembly.forums (id, name, slug, description, sort_order)
    VALUES (gen_random_uuid(), p_name, p_slug, p_description, next_order)
    RETURNING assembly.forums.id, assembly.forums.name::text, assembly.forums.slug::text, assembly.forums.description, assembly.forums.sort_order;
END;
$$;

COMMENT ON FUNCTION assembly.create_forum IS 'Create a forum with auto-assigned sort_order. Replaces forums.js:354-357 inline logic.';

-- 2. Reorder forums (bulk update from ordered ID array, single UPDATE via unnest)
CREATE OR REPLACE FUNCTION assembly.reorder_forums(
    p_ordered_ids uuid[]
)
RETURNS integer
LANGUAGE sql
AS $$
    WITH ordered AS (
        SELECT id, (ordinality - 1)::integer AS sort_order
        FROM unnest(p_ordered_ids) WITH ORDINALITY AS t(id, ordinality)
    )
    UPDATE assembly.forums AS f
    SET sort_order = ordered.sort_order
    FROM ordered
    WHERE f.id = ordered.id;
    SELECT array_length(p_ordered_ids, 1);
$$;

COMMENT ON FUNCTION assembly.reorder_forums IS 'Reorder forums by UUID array. Index 1 = sort_order 0. Replaces forums.js:400-420.';

-- 3. Soft-delete thread
CREATE OR REPLACE FUNCTION assembly.soft_delete_thread(
    p_thread_id uuid
)
RETURNS TABLE(id uuid)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    UPDATE assembly.posts
    SET expiration_dt = now()
    WHERE assembly.posts.id = p_thread_id
      AND (assembly.posts.expiration_dt = 'infinity'::timestamptz OR assembly.posts.expiration_dt > now())
    RETURNING assembly.posts.id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Thread not found' USING ERRCODE = 'P0002';
    END IF;
END;
$$;

COMMENT ON FUNCTION assembly.soft_delete_thread IS 'Soft-expire a thread. Replaces forums.js:444 inline DELETE→UPDATE.';

-- 4. Soft-delete comment
CREATE OR REPLACE FUNCTION assembly.soft_delete_comment(
    p_comment_id uuid
)
RETURNS TABLE(id uuid)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    UPDATE assembly.comments
    SET expiration_dt = now()
    WHERE assembly.comments.id = p_comment_id
      AND (assembly.comments.expiration_dt = 'infinity'::timestamptz OR assembly.comments.expiration_dt > now())
    RETURNING assembly.comments.id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Comment not found' USING ERRCODE = 'P0002';
    END IF;
END;
$$;

COMMENT ON FUNCTION assembly.soft_delete_comment IS 'Soft-expire a comment. Replaces forums.js:491 inline DELETE→UPDATE.';

-- 5. Create thread (forum check + insert)
CREATE OR REPLACE FUNCTION assembly.create_thread(
    p_forum_slug text,
    p_user_id uuid,
    p_title text,
    p_body text,
    p_source_url text DEFAULT NULL,
    p_role text DEFAULT NULL,
    p_model text DEFAULT NULL
)
RETURNS TABLE(id uuid, title text, role text, model text)
LANGUAGE plpgsql
AS $$
DECLARE
    v_forum_id uuid;
BEGIN
    SELECT f.id INTO v_forum_id
    FROM assembly.forums f
    WHERE f.slug = p_forum_slug
      AND (f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now())
    LIMIT 1;

    IF v_forum_id IS NULL THEN
        RAISE EXCEPTION 'Forum not found' USING ERRCODE = 'P0002';
    END IF;

    RETURN QUERY
    INSERT INTO assembly.posts (id, forum_uuid, posted_by_id, title, text, source_url, role, model, created)
    VALUES (gen_random_uuid(), v_forum_id, p_user_id, p_title, p_body, p_source_url, p_role, p_model, now())
    RETURNING assembly.posts.id, assembly.posts.title::text, assembly.posts.role, assembly.posts.model;
END;
$$;

COMMENT ON FUNCTION assembly.create_thread IS 'Create a thread with forum existence check. Replaces forums.js:107-127 inline logic.';

-- 6. Add comment (thread validation + parent chain validation + insert)
CREATE OR REPLACE FUNCTION assembly.add_comment(
    p_thread_id uuid,
    p_user_id uuid,
    p_body text,
    p_parent_id uuid DEFAULT NULL,
    p_role text DEFAULT NULL,
    p_model text DEFAULT NULL
)
RETURNS TABLE(id uuid, role text, model text)
LANGUAGE plpgsql
AS $$
DECLARE
    v_post_id uuid;
    v_root_post_id uuid;
BEGIN
    -- Validate thread exists and is not expired
    SELECT p.id INTO v_post_id
    FROM assembly.posts p
    JOIN assembly.forums f ON f.id = p.forum_uuid AND (f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now())
    WHERE p.id = p_thread_id
      AND (p.expiration_dt = 'infinity'::timestamptz OR p.expiration_dt > now())
    LIMIT 1;

    IF v_post_id IS NULL THEN
        RAISE EXCEPTION 'Thread not found' USING ERRCODE = 'P0002';
    END IF;

    -- If parentId provided, validate it belongs to this thread
    IF p_parent_id IS NOT NULL THEN
        WITH RECURSIVE chain AS (
            SELECT c.id, c.parent_id, c.post_id FROM assembly.comments c WHERE c.id = p_parent_id
            UNION ALL
            SELECT c.id, c.parent_id, c.post_id
            FROM assembly.comments c
            JOIN chain cc ON c.id = cc.parent_id
        )
        SELECT post_id INTO v_root_post_id FROM chain WHERE post_id IS NOT NULL LIMIT 1;

        IF v_root_post_id IS NULL OR v_root_post_id != v_post_id THEN
            RAISE EXCEPTION 'Parent comment not found or does not belong to this thread' USING ERRCODE = 'P0001';
        END IF;

        -- For threaded replies, null out post_id (comment attaches to parent)
        v_post_id := NULL;
    END IF;

    RETURN QUERY
    INSERT INTO assembly.comments (id, post_id, parent_id, text, posted_by_id, role, model, created)
    VALUES (gen_random_uuid(), v_post_id, p_parent_id, p_body, p_user_id, p_role, p_model, now())
    RETURNING assembly.comments.id, assembly.comments.role, assembly.comments.model;
END;
$$;

COMMENT ON FUNCTION assembly.add_comment IS 'Add comment with thread/parent validation. Replaces forums.js:278-317 inline logic.';

-- 7. Move thread
CREATE OR REPLACE FUNCTION assembly.move_thread(
    p_post_id uuid,
    p_forum_id uuid
)
RETURNS TABLE(id uuid, title text, forum_uuid uuid, created timestamp, updated timestamp, text text, url text, rating bigint, posted_by_id uuid, source_url text)
LANGUAGE plpgsql
AS $$
DECLARE
    v_forum_exists boolean;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM assembly.forums f
        WHERE f.id = p_forum_id
          AND (f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now())
    ) INTO v_forum_exists;

    IF NOT v_forum_exists THEN
        RAISE EXCEPTION 'Destination forum not found' USING ERRCODE = 'P0002';
    END IF;

    RETURN QUERY
    UPDATE assembly.posts p
    SET forum_uuid = p_forum_id, updated = now()
    WHERE p.id = p_post_id
      AND (p.expiration_dt = 'infinity'::timestamptz OR p.expiration_dt > now())
    RETURNING p.id, p.title::text, p.forum_uuid, p.created, p.updated, p.text, p.url::text, p.rating, p.posted_by_id, p.source_url::text;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Post not found' USING ERRCODE = 'P0002';
    END IF;
END;
$$;

COMMENT ON FUNCTION assembly.move_thread IS 'Move thread to another forum with expiration guard. Replaces forums.js:431-439.';

-- 8. Link forum to agenda (upsert with revival)
CREATE OR REPLACE FUNCTION assembly.link_forum_agenda(
    p_forum_id uuid,
    p_agenda_id uuid,
    p_label text DEFAULT NULL
)
RETURNS TABLE(forum_id uuid, agenda_id uuid, label text, created_at timestamptz)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    INSERT INTO assembly.forum_agendas (forum_id, agenda_id, label)
    VALUES (p_forum_id, p_agenda_id, p_label)
    ON CONFLICT (forum_id, agenda_id)
    DO UPDATE SET label = EXCLUDED.label, expiration_dt = 'infinity'
    RETURNING assembly.forum_agendas.forum_id, assembly.forum_agendas.agenda_id, assembly.forum_agendas.label, assembly.forum_agendas.created_at;
END;
$$;

COMMENT ON FUNCTION assembly.link_forum_agenda IS 'Link forum to agenda with upsert + expiration revival. Replaces bridges.js:13-19.';

-- 9. Unlink forum from agenda (soft-expire)
CREATE OR REPLACE FUNCTION assembly.unlink_forum_agenda(
    p_forum_id uuid,
    p_agenda_id uuid
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE assembly.forum_agendas
    SET expiration_dt = now()
    WHERE forum_id = p_forum_id
      AND agenda_id = p_agenda_id
      AND (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now());
END;
$$;

COMMENT ON FUNCTION assembly.unlink_forum_agenda IS 'Soft-expire a forum-agenda link. Replaces bridges.js:28.';

-- 10. Link post to artifact (upsert with revival)
CREATE OR REPLACE FUNCTION assembly.link_post_artifact(
    p_post_id uuid,
    p_artifact_type text,
    p_artifact_id uuid,
    p_label text DEFAULT NULL
)
RETURNS TABLE(post_id uuid, artifact_type text, artifact_id uuid, label text, created_at timestamptz)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    INSERT INTO assembly.post_artifact_refs (post_id, artifact_type, artifact_id, label)
    VALUES (p_post_id, p_artifact_type, p_artifact_id, p_label)
    ON CONFLICT (post_id, artifact_type, artifact_id)
    DO UPDATE SET label = EXCLUDED.label, expiration_dt = 'infinity'
    RETURNING assembly.post_artifact_refs.post_id, assembly.post_artifact_refs.artifact_type, assembly.post_artifact_refs.artifact_id, assembly.post_artifact_refs.label, assembly.post_artifact_refs.created_at;
END;
$$;

COMMENT ON FUNCTION assembly.link_post_artifact IS 'Link post to artifact with upsert + expiration revival. Replaces bridges.js:63-69.';

-- 11. Unlink post from artifact (soft-expire)
CREATE OR REPLACE FUNCTION assembly.unlink_post_artifact(
    p_post_id uuid,
    p_artifact_type text,
    p_artifact_id uuid
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE assembly.post_artifact_refs
    SET expiration_dt = now()
    WHERE post_id = p_post_id
      AND artifact_type = p_artifact_type
      AND artifact_id = p_artifact_id
      AND (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now());
END;
$$;

COMMENT ON FUNCTION assembly.unlink_post_artifact IS 'Soft-expire a post-artifact link. Replaces bridges.js:78.';


-- ── SEARCH HELPERS ────────────────────────────────────────────────────────

-- Escape function for LIKE/ILIKE patterns (replaces JS escapeLike)
CREATE OR REPLACE FUNCTION assembly.escape_like(p_input text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
  SELECT replace(replace(replace(p_input, '\', '\\'), '%', '\%'), '_', '\_');
$$;

COMMENT ON FUNCTION assembly.escape_like IS 'Escape special chars for LIKE/ILIKE patterns. Replaces JS escapeLike in search.js.';

-- Search forums by name, description, or slug
CREATE OR REPLACE FUNCTION assembly.search_forums(
    q text,
    lim integer DEFAULT 20
)
RETURNS TABLE(id uuid, name text, slug text, description text)
LANGUAGE sql
AS $$
    SELECT f.id, f.name, f.slug, f.description
    FROM assembly.forums f
    WHERE (f.name ILIKE ('%' || assembly.escape_like(q) || '%') ESCAPE '\'
        OR f.description ILIKE ('%' || assembly.escape_like(q) || '%') ESCAPE '\'
        OR f.slug ILIKE ('%' || assembly.escape_like(q) || '%') ESCAPE '\')
      AND (f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now())
    LIMIT lim;
$$;

COMMENT ON FUNCTION assembly.search_forums IS 'Search forums by name/description/slug with ILIKE. Replaces search.js forum query.';

-- Search posts/threads by title or body text
CREATE OR REPLACE FUNCTION assembly.search_posts(
    q text,
    lim integer DEFAULT 20
)
RETURNS TABLE(id uuid, title text, body text, forum_slug text)
LANGUAGE sql
AS $$
    SELECT p.id, p.title, p.text AS body, f.slug AS forum_slug
    FROM assembly.posts p
    JOIN assembly.forums f ON f.id = p.forum_uuid
    WHERE (p.title ILIKE ('%' || assembly.escape_like(q) || '%') ESCAPE '\'
        OR p.text ILIKE ('%' || assembly.escape_like(q) || '%') ESCAPE '\')
      AND (p.expiration_dt = 'infinity'::timestamptz OR p.expiration_dt > now())
    LIMIT lim;
$$;

COMMENT ON FUNCTION assembly.search_posts IS 'Search posts by title/body with ILIKE. Replaces search.js posts query.';

-- Search comments by body text
CREATE OR REPLACE FUNCTION assembly.search_comments(
    q text,
    lim integer DEFAULT 20
)
RETURNS TABLE(id uuid, body text, thread_id uuid, thread_title text, forum_slug text)
LANGUAGE sql
AS $$
    SELECT c.id, c.text AS body, p.id AS thread_id, p.title AS thread_title, f.slug AS forum_slug
    FROM assembly.comments c
    JOIN assembly.posts p ON p.id = c.post_id AND (p.expiration_dt = 'infinity'::timestamptz OR p.expiration_dt > now())
    JOIN assembly.forums f ON f.id = p.forum_uuid
    WHERE c.text ILIKE ('%' || assembly.escape_like(q) || '%') ESCAPE '\'
      AND (c.expiration_dt = 'infinity'::timestamptz OR c.expiration_dt > now())
    LIMIT lim;
$$;

COMMENT ON FUNCTION assembly.search_comments IS 'Search comments by body text with ILIKE. Replaces search.js comments query.';
