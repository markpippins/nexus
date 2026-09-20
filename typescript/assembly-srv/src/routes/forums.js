import { Router } from 'express';
import { pool } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';
import { requireUuid } from '../uuid-params.js';

export const forumsRouter = Router();

// ── Thread status vocabulary (posts.rating) ─────────────────────────
// Colored status indicator per thread, stored on the ROOT post's rating
// bigint. Any commenter may advance it. Canonical mapping (shared with
// assembly-ui and the backfill script — keep in sync):
//   0 posted      (default; null rating reads as 0)
//   1 specified   blue
//   2 planned     yellow
//   3 implemented orange
//   4 accepted    green
//   5 rejected    red
//   6 reopened    purple
//   7 closed      grey
//   8 approved   teal  (INTERIM scheme 2026-08-25: operator approval of a
//                    posted To Do -> candidate "in flight" ready for
//                    requirement conversion; superseded by Wind doctrine)
const STATUS_MIN = 0;
const STATUS_MAX = 8;

function normalizeStatusRating(value) {
  if (value === undefined || value === null) return 0;
  const n = Number(value);
  if (!Number.isInteger(n) || n < STATUS_MIN || n > STATUS_MAX) {
    throw new BadRequestError(`statusRating must be an integer ${STATUS_MIN}..${STATUS_MAX}`);
  }
  return n;
}

async function setThreadStatusRating(threadId, rating) {
  const result = await pool.query(
    'UPDATE assembly.posts SET rating = $2, updated = now() WHERE id = $1 AND (expiration_dt = \'infinity\'::timestamptz OR expiration_dt > now()) RETURNING id, rating',
    [threadId, rating]
  );
  if (result.rows.length === 0) throw new NotFoundError('Thread not found');
  invalidateThreadListCache();
  return { id: result.rows[0].id, statusRating: Number(result.rows[0].rating) };
}

// ── Thread list cache (in-memory TTL) ───────────────────────────────
// Transcripts-style forums (2000+ threads with full bodies) are expensive
// to re-serialize on every request. Cache the mapped list per slug+params
// for a short TTL; any write to a thread/comment invalidates the whole
// cache (write volume is low, so a coarse clear is simplest and safe).
const threadListCache = new Map();
const THREAD_LIST_CACHE_TTL_MS = 60_000;

function cacheKey(slug, includeBody, bodyWindow, paginate, page, pageSize) {
  return `${slug}|body:${includeBody ? 1 : 0}|win:${bodyWindow}|page:${paginate ? `${page}:${pageSize}` : 'all'}`;
}

function invalidateThreadListCache() {
  threadListCache.clear();
}

// ── Forum CRUD ──────────────────────────────────────────────────────

forumsRouter.get('/', async (_req, res, next) => {
  try {
    const result = await pool.query('SELECT id, name, slug, description, sort_order, thread_count, comment_count FROM assembly.forum_list_v');

    const forums = result.rows.map(row => ({
      id: row.id,
      slug: row.slug,
      name: row.name,
      description: row.description || '',
      sortOrder: row.sort_order ?? 0,
      threadCount: parseInt(row.thread_count, 10),
      postCount: parseInt(row.comment_count, 10) + parseInt(row.thread_count, 10),
    }));

    res.set('Cache-Control', 'public, max-age=60, stale-while-revalidate=300');
    res.json(forums);
  } catch (err) {
    next(err);
  }
});

// GET /:slug/threads — thread list with three progressive optimizations:
//   1. body is omitted unless ?includeBody=true — or ?bodyWindow=N, which
//      returns bodies ONLY for the N most-recent threads of the forum (by
//      post_created, on any page) so large forums like transcripts get recent
//      previews without shipping every body (~99% of the payload; the detail
//      endpoint serves the rest on demand)
//   2. pagination via ?page=&pageSize= (or ?perPage=) — when ANY pagination
//      param is present the response is an envelope
//      { items, total, page, pageSize }; without them it stays a flat array
//      for legacy consumers (Angular assembly app, duality-ui, scripts)
//   3. responses are cached in-memory for 60s and sent with a short
//      Cache-Control (public, max-age=60, stale-while-revalidate=300) so
//      browsers/ETags can revalidate instead of re-downloading
forumsRouter.get('/:slug/threads', async (req, res, next) => {
  try {
    const includeBody = req.query.includeBody === 'true';
    // bodyWindow=N: include bodies ONLY for the N most-recent threads of the
    // forum (by post_created) on any page — large forums like transcripts get
    // recent previews without shipping every body. Independent of includeBody
    // (which returns all bodies when true). Clamped to [0, 100].
    const bodyWindowParam = parseInt(/** @type {string} */ (req.query.bodyWindow), 10);
    const bodyWindow = Number.isFinite(bodyWindowParam) && bodyWindowParam > 0
      ? Math.min(bodyWindowParam, 100) : 0;
    const pageParam = parseInt(/** @type {string} */ (req.query.page), 10);
    const sizeParam = parseInt(/** @type {string} */ (req.query.pageSize ?? req.query.perPage), 10);
    const paginate = Number.isFinite(pageParam) || Number.isFinite(sizeParam);
    const page = Number.isFinite(pageParam) && pageParam > 0 ? pageParam : 1;
    const pageSize = Number.isFinite(sizeParam) && sizeParam > 0 ? Math.min(sizeParam, 500) : 100;

    const key = cacheKey(req.params.slug, includeBody, bodyWindow, paginate, page, pageSize);
    const cached = threadListCache.get(key);
    if (cached && Date.now() - cached.at < THREAD_LIST_CACHE_TTL_MS) {
      res.set('Cache-Control', 'public, max-age=60, stale-while-revalidate=300');
      return res.json(cached.body);
    }

    // Explicit column list (not SELECT *) so `text` (body) can be omitted.
    const baseCols = `
      post_id, title, post_created, role, model,
      user_id, alias, avatar_url, forum_id, forum_slug, forum_name,
      reply_count, last_reply_at, last_reply_user_alias, rating`;
    const cols = includeBody ? `${baseCols}, text` : baseCols;

    // Do not silently return 200 [] for unknown or expired forums.
    // Distinguish "forum missing" from "forum exists but has no threads".
    {
      const forumCheck = await pool.query(
        `SELECT id FROM assembly.forums WHERE slug = $1
         AND (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now())
         LIMIT 1`,
        [req.params.slug]
      );
      if (forumCheck.rows.length === 0) {
        throw new NotFoundError('Forum not found');
      }
    }

    let result;
    let total = null;
    if (paginate) {
      const [listResult, countResult] = await Promise.all([
        pool.query(
          `SELECT ${cols} FROM assembly.thread_list_v WHERE forum_slug = $1
           ORDER BY post_created DESC LIMIT $2 OFFSET $3`,
          [req.params.slug, pageSize, (page - 1) * pageSize]
        ),
        pool.query(
          'SELECT count(*)::int AS total FROM assembly.thread_list_v WHERE forum_slug = $1',
          [req.params.slug]
        ),
      ]);
      result = listResult;
      total = countResult.rows[0]?.total ?? 0;
    } else {
      result = await pool.query(
        `SELECT ${cols} FROM assembly.thread_list_v WHERE forum_slug = $1
         ORDER BY post_created DESC`,
        [req.params.slug]
      );
    }

    // bodyWindow>0: fetch bodies for the N most-recent threads of the forum
    // (independent of pagination) and merge by post_id. Keeps the main query
    // body-less for large forums while still returning recent previews.
    let recentBodies = null;
    if (bodyWindow > 0) {
      const recent = await pool.query(
        `SELECT post_id, text FROM assembly.thread_list_v
         WHERE forum_slug = $1 ORDER BY post_created DESC LIMIT $2`,
        [req.params.slug, bodyWindow]
      );
      recentBodies = new Map(recent.rows.map(r => [r.post_id, r.text || '']));
    }

    const threads = result.rows.map(row => ({
      id: row.post_id,
      title: row.title || 'Untitled',
      body: includeBody
        ? (row.text || '')
        : (recentBodies?.get(row.post_id) ?? undefined),
      statusRating: row.rating == null ? 0 : Number(row.rating),
      role: row.role || null,
      model: row.model || null,
      createdAt: new Date(row.post_created).toISOString(),
      replyCount: parseInt(row.reply_count, 10),
      viewCount: 0,
      lastReplyAt: row.last_reply_at ? new Date(row.last_reply_at).toISOString() : null,
      lastReplyAuthor: row.last_reply_user_alias,
      author: {
        id: row.user_id,
        name: row.alias,
        avatar: row.avatar_url || '',
      },
      forum: {
        id: row.forum_id,
        slug: row.forum_slug,
        name: row.forum_name,
      },
    }));

    const body = paginate ? { items: threads, total, page, pageSize } : threads;
    threadListCache.set(key, { at: Date.now(), body });
    res.set('Cache-Control', 'public, max-age=60, stale-while-revalidate=300');
    res.json(body);
  } catch (err) {
    next(err);
  }
});

forumsRouter.post('/:slug/threads', async (req, res, next) => {
  try {
    const { title, body, postedById, source_url, role, model } = req.body;
    if (!title || !body) {
      throw new BadRequestError('Title and body are required');
    }

    const userId = postedById;
    if (!userId) {
      throw new BadRequestError('postedById is required');
    }

    const result = await pool.query(
      'SELECT * FROM assembly.create_thread($1, $2, $3, $4, $5, $6, $7)',
      [req.params.slug, userId, String(title).slice(0, 500), String(body), source_url || null, role || null, model || null]
    );
    invalidateThreadListCache();

    res.status(201).json({ id: result.rows[0].id, title: result.rows[0].title, role: result.rows[0].role, model: result.rows[0].model });
  } catch (err) {
    next(err);
  }
});

// ── UUID-based thread endpoints (avoids slug resolution round-trip) ──

forumsRouter.post('/by-id/:forumId/threads', async (req, res, next) => {
  try {
    const { title, body, postedById, source_url, role, model } = req.body;
    if (!title || !body) throw new BadRequestError('Title and body are required');
    if (!postedById) throw new BadRequestError('postedById is required');

    const forumCheck = await pool.query(
      'SELECT id FROM assembly.forums WHERE id = $1 AND (expiration_dt = \'infinity\'::timestamptz OR expiration_dt > now()) LIMIT 1',
      [requireUuid(req.params.forumId, 'forumId')]
    );
    if (forumCheck.rows.length === 0) throw new NotFoundError('Forum not found');

    const result = await pool.query(
      `INSERT INTO assembly.posts (id, forum_uuid, posted_by_id, title, text, source_url, role, model, created)
       VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, NOW())
       RETURNING id, title, role, model`,
      [requireUuid(req.params.forumId, 'forumId'), postedById, String(title).slice(0, 500), String(body), source_url || null, role || null, model || null]
    );
    invalidateThreadListCache();
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

forumsRouter.get('/by-id/:forumId/threads', async (req, res, next) => {
  try {
    // Do not silently return 200 [] for unknown or expired forums.
    // Distinguish "forum missing" from "forum exists but has no threads".
    {
      const forumCheck = await pool.query(
        `SELECT id FROM assembly.forums WHERE id = $1
         AND (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now())
         LIMIT 1`,
        [requireUuid(req.params.forumId, 'forumId')]
      );
      if (forumCheck.rows.length === 0) {
        throw new NotFoundError('Forum not found');
      }
    }

    const result = await pool.query(
      `SELECT p.id, p.title, p.created, p.text, p.source_url, p.role, p.model, p.rating,
              u.id AS user_id, u.alias, u.avatar_url,
              f.id AS forum_id, f.slug AS forum_slug, f.name AS forum_name
       FROM assembly.posts p
       JOIN assembly.forums f ON f.id = p.forum_uuid
       JOIN assembly.users u ON u.id = p.posted_by_id
       WHERE p.forum_uuid = $1 AND (p.expiration_dt = 'infinity'::timestamptz OR p.expiration_dt > now())
       ORDER BY p.created DESC`,
      [requireUuid(req.params.forumId, 'forumId')]
    );
    res.set('Cache-Control', 'public, max-age=60, stale-while-revalidate=300');
    res.json(result.rows);
  } catch (err) { next(err); }
});

forumsRouter.get('/threads/:threadId', async (req, res, next) => {
  try {
    const threadResult = await pool.query(`
      SELECT
        p.id AS post_id,
        p.title,
        p.text,
        p.created AS post_created,
        p.role,
        p.model,
        p.rating,
        u.id AS user_id,
        u.alias,
        u.avatar_url,
        f.id AS forum_id,
        f.slug AS forum_slug,
        f.name AS forum_name
      FROM assembly.posts p
      JOIN assembly.forums f ON f.id = p.forum_uuid AND (f.expiration_dt = 'infinity'::timestamptz OR f.expiration_dt > now())
      JOIN assembly.users u ON u.id = p.posted_by_id
      WHERE p.id = $1 AND (p.expiration_dt = 'infinity'::timestamptz OR p.expiration_dt > now())
      LIMIT 1
    `, [requireUuid(req.params.threadId, 'threadId')]);

    if (threadResult.rows.length === 0) {
      throw new NotFoundError('Thread not found');
    }

    const row = threadResult.rows[0];

    const commentsResult = await pool.query(`
      WITH RECURSIVE comment_tree AS (
        SELECT c.*, 0 AS depth
        FROM assembly.comments c
        WHERE c.post_id = $1 AND (c.expiration_dt = 'infinity'::timestamptz OR c.expiration_dt > now())
        UNION ALL
        SELECT c.*, ct.depth + 1
        FROM assembly.comments c
        JOIN comment_tree ct ON c.parent_id = ct.id
        WHERE (c.expiration_dt = 'infinity'::timestamptz OR c.expiration_dt > now())
      )
      SELECT
        ct.id AS comment_id,
        ct.post_id,
        ct.parent_id,
        ct.text,
        ct.created AS comment_created,
        ct.role,
        ct.model,
        u.id AS user_id,
        u.alias,
        u.avatar_url
      FROM comment_tree ct
      JOIN assembly.users u ON u.id = ct.posted_by_id
      ORDER BY ct.depth ASC, ct.created ASC
    `, [requireUuid(req.params.threadId, 'threadId')]);

    const comments = commentsResult.rows.map(c => ({
      id: c.comment_id,
      body: c.text || '',
      role: c.role || null,
      model: c.model || null,
      createdAt: new Date(c.comment_created).toISOString(),
      parentId: c.parent_id || null,
      author: {
        id: c.user_id,
        name: c.alias,
        avatar: c.avatar_url || '',
      },
    }));

    res.json({
      thread: {
        id: row.post_id,
        title: row.title || 'Untitled',
        body: row.text || '',
        statusRating: row.rating == null ? 0 : Number(row.rating),
        role: row.role || null,
        model: row.model || null,
        createdAt: new Date(row.post_created).toISOString(),
        author: {
          id: row.user_id,
          name: row.alias,
          avatar: row.avatar_url || '',
        },
        forum: {
          id: row.forum_id,
          slug: row.forum_slug,
          name: row.forum_name,
        },
      },
      comments,
    });
  } catch (err) {
    next(err);
  }
});

forumsRouter.post('/threads/:threadId/comments', async (req, res, next) => {
  try {
    const { body, postedById, parentId, role, model, statusRating } = req.body;
    if (!body || !postedById) {
      throw new BadRequestError('Body and postedById are required');
    }

    // Optional status advance: a commenter may set the thread status in the
    // same gesture as replying. Validated 0..7; null/undefined = no change.
    const newStatus = statusRating === undefined || statusRating === null
      ? null
      : normalizeStatusRating(statusRating);

    const result = await pool.query(
      'SELECT * FROM assembly.add_comment($1, $2, $3, $4, $5, $6)',
      [requireUuid(req.params.threadId, 'threadId'), postedById, String(body), parentId || null, role || null, model || null]
    );
    let status = null;
    if (newStatus !== null) {
      status = await setThreadStatusRating(requireUuid(req.params.threadId, 'threadId'), newStatus);
    }
    invalidateThreadListCache();

    res.status(201).json({ id: result.rows[0].id, role: result.rows[0].role, model: result.rows[0].model, statusRating: status ? status.statusRating : undefined });
  } catch (err) {
    // NOTE: must call next(), not throw — throw inside an async handler rejects the
    // promise unhandled, Express never responds, and the client hangs.
    if (err.code === 'P0002') return next(new NotFoundError('Thread not found'));
    if (err.code === 'P0001') return next(new BadRequestError('Parent comment not found or does not belong to this thread'));
    next(err);
  }
});

// PUT /threads/:threadId — update a thread's title/body in place (used by
// scheduled syncs like sonar-forum-sync to keep grouped-thread bodies current;
// authorship/role/model are preserved).  { title?, body? }
forumsRouter.put('/threads/:threadId', async (req, res, next) => {
  try {
    const { title, body } = req.body ?? {};
    if (!title && !body) throw new BadRequestError('title or body is required');
    const result = await pool.query(
      `UPDATE assembly.posts
       SET title = COALESCE($2, title),
           text  = COALESCE($3, text),
           updated = now()
       WHERE id = $1 AND (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now())
       RETURNING id, title`,
      [requireUuid(req.params.threadId, 'threadId'), title == null ? null : String(title).slice(0, 500), body == null ? null : String(body)]
    );
    if (result.rows.length === 0) throw new NotFoundError('Thread not found');
    invalidateThreadListCache();
    res.json(result.rows[0]);
  } catch (err) {
    next(err);
  }
});

// PUT /threads/:threadId/status — set the colored status indicator on a
// thread (root post rating). Any commenter may update; no auth by design
// (assembly is an internal, identity-by-convention system). Body:
//   { "rating": 0..7 }  (also accepts "statusRating" alias)
forumsRouter.put('/threads/:threadId/status', async (req, res, next) => {
  try {
    const raw = req.body?.rating ?? req.body?.statusRating;
    const rating = normalizeStatusRating(raw);
    const out = await setThreadStatusRating(requireUuid(req.params.threadId, 'threadId'), rating);
    res.json(out);
  } catch (err) {
    if (err.code === '23503' || err.code === '22P02') return next(new NotFoundError('Thread not found'));
    next(err);
  }
});

// ── Forum management (missing from original — migrated from assembly-mcp db.ts) ──

forumsRouter.get('/by-slug/:slug', async (req, res, next) => {
  try {
    const result = await pool.query(
      'SELECT id, name, slug, description FROM assembly.forums WHERE slug = $1 AND (expiration_dt = \'infinity\'::timestamptz OR expiration_dt > now()) LIMIT 1',
      [req.params.slug]
    );
    if (result.rows.length === 0) throw new NotFoundError('Forum not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

forumsRouter.get('/by-id/:id', async (req, res, next) => {
  try {
    const result = await pool.query(
      'SELECT id, name, slug, description FROM assembly.forums WHERE id = $1',
      [requireUuid(req.params.id, 'id')]
    );
    if (result.rows.length === 0) throw new NotFoundError('Forum not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

forumsRouter.post('/', async (req, res, next) => {
  try {
    const { name, slug, description } = req.body;
    if (!name) throw new BadRequestError('name is required');
    const genSlug = slug || name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(?:^-)|(?:-$)/g, '');
    const result = await pool.query(
      'SELECT * FROM assembly.create_forum($1, $2, $3)',
      [name, genSlug, description || null]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

forumsRouter.put('/:id', async (req, res, next) => {
  try {
    const { name, slug, description } = req.body;
    const sets = [];
    const params = [];
    let idx = 1;
    if (name !== undefined) { sets.push(`name = $${idx++}`); params.push(name); }
    if (slug !== undefined) { sets.push(`slug = $${idx++}`); params.push(slug); }
    if (description !== undefined) { sets.push(`description = $${idx++}`); params.push(description); }
    if (sets.length === 0) {
      const r = await pool.query('SELECT id, name, slug, description FROM assembly.forums WHERE id = $1', [requireUuid(req.params.id, 'id')]);
      if (r.rows.length === 0) throw new NotFoundError('Forum not found');
      return res.json(r.rows[0]);
    }
    params.push(requireUuid(req.params.id, 'id'));
    const result = await pool.query(
      `UPDATE assembly.forums SET ${sets.join(', ')} WHERE id = $${idx} RETURNING id, name, slug, description`,
      params
    );
    if (result.rows.length === 0) throw new NotFoundError('Forum not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

forumsRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await pool.query(
      'UPDATE assembly.forums SET expiration_dt = now() WHERE id = $1 RETURNING id, name',
      [requireUuid(req.params.id, 'id')]
    );
    if (result.rows.length === 0) throw new NotFoundError('Forum not found');
    res.json({ expired: true, forum_id: req.params.id, name: result.rows[0].name });
  } catch (err) { next(err); }
});

// ── Reorder ───────────────────────────────────────────────────────────

forumsRouter.put('/reorder', async (req, res, next) => {
  try {
    const { orderedIds } = req.body;
    if (!Array.isArray(orderedIds) || orderedIds.length === 0) {
      throw new BadRequestError('orderedIds array is required');
    }
    const result = await pool.query(
      'SELECT assembly.reorder_forums($1::uuid[])',
      [orderedIds]
    );
    const count = result.rows[0]?.reorder_forums ?? orderedIds.length;
    res.json({ reordered: true, count });
  } catch (err) { next(err); }
});

// ── Thread management ───────────────────────────────────────────────

forumsRouter.post('/move-thread', async (req, res, next) => {
  try {
    const { post_id, forum_id } = req.body;
    if (!post_id || !forum_id) throw new BadRequestError('post_id and forum_id are required');
    const result = await pool.query(
      'SELECT * FROM assembly.move_thread($1, $2)',
      [post_id, forum_id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Post not found');
    res.json(result.rows[0]);
  } catch (err) {
    if (err.code === 'P0002') return next(new NotFoundError('Destination forum not found or post not found'));
    next(err);
  }
});forumsRouter.delete('/threads/:threadId', async (req, res, next) => {
  try {
    const result = await pool.query(
      'SELECT * FROM assembly.soft_delete_thread($1)',      [requireUuid(req.params.threadId, 'threadId')]

    );
    if (result.rowCount === 0) throw new NotFoundError('Thread not found');
    invalidateThreadListCache();
    res.json({ deleted: true, expired: true, thread_id: req.params.threadId });
  } catch (err) {
    if (err.code === 'P0002') return next(new NotFoundError('Thread not found'));
    next(err);
  }
});

// ── Search ──────────────────────────────────────────────────────────

forumsRouter.get('/search/by-name', async (req, res, next) => {
  try {
    const { name } = req.query;
    if (!name) throw new BadRequestError('name query parameter is required');
    const result = await pool.query(
      'SELECT id, name, slug, description FROM assembly.forums WHERE name ILIKE $1 OR slug ILIKE $1 ORDER BY name ASC LIMIT 20',
      [`%${name}%`]
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

forumsRouter.get('/search/by-thread-title', async (req, res, next) => {
  try {
    const { title } = req.query;
    if (!title) throw new BadRequestError('title query parameter is required');
    const result = await pool.query(
      'SELECT id, created, updated, text, url, rating, posted_by_id, forum_uuid, source_url, title FROM assembly.posts WHERE title ILIKE $1 ORDER BY created DESC LIMIT 20',
      [`%${title}%`]
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// ── Comment management ──────────────────────────────────────────────

forumsRouter.get('/comments/:id', async (req, res, next) => {
  try {
    const result = await pool.query(
      'SELECT id, created, updated, text, url, rating, posted_by_id, post_id, parent_id FROM assembly.comments WHERE id = $1',
      [requireUuid(req.params.id, 'id')]
    );
    if (result.rows.length === 0) throw new NotFoundError('Comment not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// PUT /forums/comments/:id — edit a comment's body. Body: { body (**req**) }.
// Soft-adjacent semantics: keeps created timestamp, bumps updated.
forumsRouter.put('/comments/:id', async (req, res, next) => {
  try {
    const { body } = req.body;
    if (!body || !String(body).trim()) {
      throw new BadRequestError('body is required');
    }
    const result = await pool.query(
      `UPDATE assembly.comments
       SET text = $2, updated = now()
       WHERE id = $1
         AND (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now())
       RETURNING id, post_id, parent_id, text AS body, role, model,
                 created AS "createdAt", updated`,
      [requireUuid(req.params.id, 'id'), String(body)]
    );
    if (result.rows.length === 0) throw new NotFoundError('Comment not found');
    invalidateThreadListCache();
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// DELETE /threads/:threadId/comments — soft-delete ALL comments on a thread
// (bulk refresh support: re-emitting per-turn transcript comments after a
// format change). Same expiration semantics as the single-comment delete.
forumsRouter.delete('/threads/:threadId/comments', async (req, res, next) => {
  try {
    const result = await pool.query(
      `UPDATE assembly.comments
       SET expiration_dt = now()
       WHERE post_id = $1
         AND (expiration_dt = 'infinity'::timestamptz OR expiration_dt > now())`,      [requireUuid(req.params.threadId, 'threadId')]

    );
    invalidateThreadListCache();
    res.json({ deleted: result.rowCount, expired: true, thread_id: req.params.threadId });
  } catch (err) {
    // invalid uuid in path
    if (err.code === '22P02') return next(new BadRequestError('Invalid thread id'));
    next(err);
  }
});

forumsRouter.delete('/comments/:id', async (req, res, next) => {
  try {
    const result = await pool.query(
      'SELECT * FROM assembly.soft_delete_comment($1)',
      [requireUuid(req.params.id, 'id')]
    );
    if (result.rowCount === 0) throw new NotFoundError('Comment not found');
    invalidateThreadListCache();
    res.json({ deleted: true, expired: true, comment_id: req.params.id });
  } catch (err) {
    if (err.code === 'P0002') return next(new NotFoundError('Comment not found'));
    next(err);
  }
});
