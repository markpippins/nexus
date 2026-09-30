import { Router } from 'express';
import { randomUUID } from 'crypto';
import { pool } from '../db.js';
import { BadRequestError } from '../errors.js';

export const dualityRouter = Router();

// ── Session Watches ──────────────────────────────────────────────────

/**
 * Lazy stale-watch expiry (TTL). Watches that have been idle longer than
 * GREATEST(idle_timeout_ms, 1h) are marked 'expired' and drop out of session
 * resume lookups. Runs opportunistically on the watch reads so no background
 * sweeper is needed; 'expired' is excluded from resume but the row is kept
 * for audit. The 1h floor prevents aggressive 5-minute timeouts from
 * orphaning a human chat session between turns.
 */
async function expireStaleWatches() {
  try {
    await pool.query(
      `UPDATE duality.session_watches
       SET status = 'expired', updated_at = now()
       WHERE status IN ('active', 'paused')
         AND last_activity < now() - (GREATEST(idle_timeout_ms, 3600000) || ' milliseconds')::interval`
    );
  } catch (err) {
    console.error('[duality] stale-watch expiry failed:', err.message);
  }
}

/** POST /api/duality/watches — create (or reactivate) a session watch for a thread.
 *
 *  UPSERT on the (thread_id, role) unique constraint: a watch that was
 *  closed by the subscriber (e.g. lease-gate failure) is reactivated rather
 *  than inserted again, so a session the UI resumes keeps exactly one watch
 *  row and stays processable by the subscriber.
 */
dualityRouter.post('/watches', async (req, res, next) => {
  try {
    const { threadId, forumSlug, role, executionBackend, maxTurns, idleTimeoutMs, leaseId } = req.body;
    if (!threadId || !forumSlug || !role) {
      throw new BadRequestError('threadId, forumSlug, and role are required');
    }
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(threadId))) {
      throw new BadRequestError('threadId must be a UUID');
    }
    if (leaseId !== undefined && leaseId !== null
        && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(leaseId))) {
      throw new BadRequestError('leaseId must be a UUID');
    }
    const backend = executionBackend || 'freebuff';
    if (backend === 'freebuff' && !leaseId) {
      throw new BadRequestError('leaseId is required for freebuff watches');
    }
    if (leaseId) {
      const leaseResult = await pool.query(
        `SELECT id, role, channel, status, expires_at, budget_units, consumed_units
         FROM tackle.role_leases
         WHERE id = $1::uuid`,
        [leaseId]
      );
      const lease = leaseResult.rows[0];
      if (!lease) throw new BadRequestError('leaseId does not reference a role lease');
      if (lease.role !== role) throw new BadRequestError('leaseId role does not match watch role');
      if (backend === 'freebuff' && lease.channel !== 'interactive') {
        throw new BadRequestError('freebuff watches require an interactive role lease');
      }
      if (lease.status !== 'ACTIVE') throw new BadRequestError('leaseId must reference an ACTIVE role lease');
      if (lease.expires_at && new Date(lease.expires_at).getTime() <= Date.now()) {
        throw new BadRequestError('leaseId references an expired role lease');
      }
      if (lease.budget_units !== null && lease.consumed_units >= lease.budget_units) {
        throw new BadRequestError('leaseId references an exhausted role lease');
      }
    }
    const result = await pool.query(
      `INSERT INTO duality.session_watches
         (thread_id, forum_slug, role, execution_backend, max_turns, idle_timeout_ms,
          lease_id, turn_count, status, last_activity, created_at, updated_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, 0, 'active', now(), now(), now())
       ON CONFLICT (thread_id, role) DO UPDATE SET
         status = 'active',
         execution_backend = EXCLUDED.execution_backend,
         max_turns = EXCLUDED.max_turns,
         idle_timeout_ms = EXCLUDED.idle_timeout_ms,
         lease_id = EXCLUDED.lease_id,
         turn_count = 0,
         last_activity = now(),
         updated_at = now()
       RETURNING id`,
      [
        threadId,
        forumSlug,
        role,
        backend,
        maxTurns ?? 20,
        idleTimeoutMs ?? 300_000,
        leaseId ?? null,
      ]
    );
    const watchId = result.rows[0].id;
    // Emit a durable watch.status envelope so the SSE event stream covers
    // the session from creation (P1 item 4). Idempotent via event_key — a
    // reactivated watch (UPSERT on the same row) does not double-emit.
    try {
      await pool.query(
        `INSERT INTO duality.session_events
           (thread_id, watch_id, event_type, event_key, payload)
         VALUES ($1::uuid, $2::uuid, 'watch.status', $3, $4::jsonb)
         ON CONFLICT (event_key) DO NOTHING`,
        [
          threadId,
          watchId,
          `watch.created:${watchId}`,
          JSON.stringify({
            status: 'active',
            role,
            execution_backend: backend,
            forum_slug: forumSlug,
            lease_id: leaseId ?? null,
          }),
        ]
      );
    } catch (err) {
      console.error('[duality] watch.status event failed:', err.message);
    }
    res.status(201).json({ id: watchId, lease_id: leaseId ?? null, execution_backend: backend });
  } catch (err) { next(err); }
});

/** GET /api/duality/watches/active?role=X&forumSlug=Y[&execution_backend=Z]
 *  — find the most recent session thread for a role (+ backend when given).
 *
 *  Replaces localStorage for session persistence across browser clears.
 *  The optional execution_backend filter keeps freebuff and harness sessions
 *  from being confused with each other (previously the backend-blind lookup
 *  returned the wrong session type, and the UI silently created a new thread
 *  instead of resuming the existing one).
 *
 *  Deliberately returns the most recent watch of ANY status except
 *  'expired': a session closed by the subscriber (e.g. lease-gate failure)
 *  must stay resumable so its error history remains visible — otherwise the
 *  user's message appears to "disappear" into an orphaned thread the UI can
 *  never reach. Only stale watches (idle past the TTL) are expired and drop
 *  out of resume.
 */
dualityRouter.get('/watches/active', async (req, res, next) => {
  try {
    await expireStaleWatches();
    const { role, forumSlug, execution_backend } = req.query;
    if (!role || !forumSlug) {
      throw new BadRequestError('role and forumSlug query params are required');
    }
    const params = [role, forumSlug];
    let backendClause = '';
    if (execution_backend) {
      params.push(String(execution_backend));
      backendClause = ` AND execution_backend = $${params.length}`;
    }
    const result = await pool.query(
      `SELECT thread_id, role, execution_backend, lease_id, status, last_activity
       FROM duality.session_watches
       WHERE role = $1 AND forum_slug = $2 AND status <> 'expired'${backendClause}
       ORDER BY last_activity DESC
       LIMIT 1`,
      params
    );
    if (result.rows.length === 0) {
      return res.json({ threadId: null });
    }
    // Verify the thread still exists
    const threadCheck = await pool.query(
      'SELECT id FROM assembly.posts WHERE id = $1',
      [result.rows[0].thread_id]
    );
    if (threadCheck.rows.length === 0) {
      return res.json({ threadId: null });
    }
    const row = result.rows[0];
    res.json({
      threadId: row.thread_id,
      role: row.role,
      status: row.status,
      execution_backend: row.execution_backend,
      lease_id: row.lease_id,
    });
  } catch (err) { next(err); }
});

/** GET /api/duality/watches/:threadId — get watches for a thread. */
dualityRouter.get('/watches/:threadId', async (req, res, next) => {
  try {
    await expireStaleWatches();
    const result = await pool.query(
      `SELECT id, thread_id, forum_slug, role, execution_backend, lease_id,
              max_turns, turn_count, idle_timeout_ms, status, last_activity, created_at
       FROM duality.session_watches
       WHERE thread_id = $1 AND status = 'active'
       ORDER BY created_at DESC`,
      [req.params.threadId]
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// ── Turn/job state envelope (P0-1 item 3) ──────────────────────────────
// Server-side turn state so the UI renders the turn lifecycle instead of
// inferring it from comment count. Rows are written by the interactive-turn
// subscriber (duality.session_turns); this route is the read path.

/** GET /api/duality/turns?threadId=X[&limit=N][&state=...]
 *  — turn state envelopes for a thread, newest first.
 *
 *  Each envelope: { id (turn_id), thread_id, role, execution_backend, state,
 *  request_comment_id, response_comment_id, subscriber_id, job_id,
 *  lease_id, execution_plan_version, failure_detail, *_at timestamps }.
 *
 *  The optional state filter (accepted|running|completed|failed|timed_out|
 *  cancelled) lets the UI ask specifically for the in-flight turn.
 */
dualityRouter.get('/turns', async (req, res, next) => {
  try {
    const { threadId, limit, state } = req.query;
    if (!threadId) {
      throw new BadRequestError('threadId query param is required');
    }
    const params = [threadId];
    let stateClause = '';
    if (state) {
      const allowed = ['accepted', 'running', 'completed', 'failed', 'timed_out', 'cancelled'];
      if (!allowed.includes(String(state))) {
        throw new BadRequestError(`state must be one of: ${allowed.join(', ')}`);
      }
      params.push(String(state));
      stateClause = ` AND state = $${params.length}`;
    }
    const lim = Math.min(Math.max(Number(limit) || 10, 1), 100);
    params.push(String(lim));
    const result = await pool.query(
      `SELECT id, thread_id, watch_id, role, execution_backend, lease_id, state,
              request_comment_id, response_comment_id, subscriber_id, job_id,
              execution_plan_version, failure_detail,
              created_at, updated_at, accepted_at, running_at, completed_at,
              failed_at, timed_out_at, cancelled_at
       FROM duality.session_turns
       WHERE thread_id = $1${stateClause}
       ORDER BY created_at DESC
       LIMIT $${params.length}`,
      params
    );
    res.json({ turns: result.rows });
  } catch (err) { next(err); }
});

/** GET /api/duality/turns/latest?threadId=X — the most recent turn envelope
 *  for a thread (any state), so the UI has a single in-flight/terminal
 *  state to render without paging. */
dualityRouter.get('/turns/latest', async (req, res, next) => {
  try {
    const { threadId } = req.query;
    if (!threadId) {
      throw new BadRequestError('threadId query param is required');
    }
    const result = await pool.query(
      `SELECT id, thread_id, watch_id, role, execution_backend, lease_id, state,
              request_comment_id, response_comment_id, subscriber_id, job_id,
              execution_plan_version, failure_detail,
              created_at, updated_at, accepted_at, running_at, completed_at,
              failed_at, timed_out_at, cancelled_at
       FROM duality.session_turns
       WHERE thread_id = $1
       ORDER BY created_at DESC
       LIMIT 1`,
      [threadId]
    );
    res.json({ turn: result.rows[0] ?? null });
  } catch (err) { next(err); }
});

/** POST /api/duality/sessions/:threadId/messages — event-first message entry
 *  (P2 item 9). The durable event stream is the source of truth; the
 *  Assembly comment is a rendering projection.
 *
 *  Flow:
 *    1. write a `comment.created` event (canonical comment_id) to
 *       duality.session_events — this is the source, and its AFTER INSERT
 *       trigger NOTIFYs the subscriber to dispatch the turn,
 *    2. project the Assembly comment via assembly.add_comment (render),
 *    3. link the rendered comment id back onto the event payload.
 *
 *  Body: { body, role = 'user', postedById, model? }
 *  Returns 201 { comment_id, assembly_comment_id, thread_id }.
 */
dualityRouter.post('/sessions/:threadId/messages', async (req, res, next) => {
  try {
    const { threadId } = req.params;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(threadId)) {
      throw new BadRequestError('threadId must be a UUID');
    }
    const { body, postedById, model } = req.body;
    if (!body || !String(body).trim()) {
      throw new BadRequestError('body is required');
    }
    if (!postedById) {
      throw new BadRequestError('postedById is required');
    }
    const role = req.body.role || 'user';
    const commentId = randomUUID();

    // 0. The thread must exist before we write an event (session_events has
    //    no FK, so an event for a nonexistent thread would orphan silently).
    const threadCheck = await pool.query(
      'SELECT 1 FROM assembly.posts WHERE id = $1 LIMIT 1',
      [threadId]
    );
    if (threadCheck.rows.length === 0) {
      throw new BadRequestError('threadId does not reference an existing thread');
    }

    // 1+2. Event FIRST + projection, in ONE transaction. PostgreSQL
    //    notifications (pg_notify) are delivered at COMMIT, so writing the
    //    event and its Assembly projection atomically means the subscriber
    //    is NOTIFIED only after BOTH are durable — the dispatch can never
    //    observe the event before the comment it should read exists (no
    //    event-vs-comment race). On projection failure only the projection
    //    is rolled back (SAVEPOINT) so the event survives — the stream stays
    //    authoritative and append-only.
    const client = await pool.connect();
    let assemblyCommentId = null;
    try {
      await client.query('BEGIN');

      // Event FIRST — the durable source of truth.
      await client.query(
        `INSERT INTO duality.session_events
           (thread_id, event_type, event_key, payload)
         VALUES ($1::uuid, 'comment.created', $2, $3::jsonb)
         ON CONFLICT (event_key) DO NOTHING`,
        [
          threadId,
          `comment:${commentId}`,
          JSON.stringify({
            comment_id: commentId,
            thread_id: threadId,
            role,
            body: String(body),
            model: model ?? null,
          }),
        ]
      );

      // Project the Assembly comment (render) — the same add_comment path
      // the legacy POST /forums/threads/:id/comments uses.
      await client.query('SAVEPOINT projection');
      try {
        const projected = await client.query(
          'SELECT * FROM assembly.add_comment($1, $2, $3, NULL, $4, $5)',
          [threadId, postedById, String(body), role, model ?? null]
        );
        assemblyCommentId = projected.rows[0]?.id ?? null;
      } catch (err) {
        await client.query('ROLLBACK TO SAVEPOINT projection');
        console.error('[duality] comment projection failed (event preserved):', err.message);
      }

      await client.query('COMMIT');
    } catch (err) {
      await client.query('ROLLBACK').catch(() => {});
      client.release();
      return next(err);
    }
    client.release();

    // 3. Link the rendered comment id back onto the event payload.
    if (assemblyCommentId) {
      await pool.query(
        `UPDATE duality.session_events
         SET payload = payload || jsonb_build_object('assembly_comment_id', $1::text)
         WHERE event_key = $2`,
        [assemblyCommentId, `comment:${commentId}`]
      ).catch(() => {});
    }

    res.status(201).json({
      comment_id: commentId,
      assembly_comment_id: assemblyCommentId,
      thread_id: threadId,
      projected: Boolean(assemblyCommentId),
    });
  } catch (err) { next(err); }
});

/** GET /api/duality/turns/:turnId — a single turn envelope. */
dualityRouter.get('/turns/:turnId', async (req, res, next) => {
  try {
    const result = await pool.query(
      `SELECT id, thread_id, watch_id, role, execution_backend, lease_id, state,
              request_comment_id, response_comment_id, subscriber_id, job_id,
              execution_plan_version, failure_detail,
              created_at, updated_at, accepted_at, running_at, completed_at,
              failed_at, timed_out_at, cancelled_at
       FROM duality.session_turns
       WHERE id = $1`,
      [req.params.turnId]
    );
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'turn not found' });
    }
    res.json({ turn: result.rows[0] });
  } catch (err) { next(err); }
});

// ── Session event stream — replayable SSE (P1 items 4-5) ─────────────
// GET /api/duality/sessions/:threadId/events?after=<seq>
//
// Replaces count-based change detection with a replayable typed event
// stream backed by the durable duality.session_events log (V113).
//
// Wire format (SSE):
//   event: connected      data: { threadId, after, seq, replayed }
//   event: heartbeat      data: { seq }                    (every 15s)
//   event: <event_type>   data: { seq, eventType, threadId, turnId,
//                                 watchId, payload, createdAt }
//
// event_type ∈ turn.accepted | turn.started | thinking | comment.created |
//             turn.completed | turn.failed | turn.timed_out |
//             turn.cancelled | watch.status
//
// Replay: events with seq > after (when after is given) are replayed
// immediately; afterwards the stream follows live inserts via PG LISTEN on
// the duality_session_events channel (fired by the V113 AFTER INSERT
// trigger). The browser reconnects with after=<last seen seq> — no
// full-thread refetch. Assembly REST stays the command + finite-history
// surface; this endpoint is observation only.

/** Write one SSE frame: `event: <type>\ndata: <json>\n\n`. */
function sendSse(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

/** Map a session_events row to the SSE envelope shape. */
function eventEnvelope(row) {
  return {
    seq: Number(row.seq),
    eventType: row.event_type,
    threadId: row.thread_id,
    turnId: row.turn_id ?? null,
    watchId: row.watch_id ?? null,
    payload: row.payload ?? {},
    createdAt: row.created_at,
  };
}

/** Fetch events for a thread with seq > cursor (ordered, capped). */
async function fetchEventsAfter(threadId, cursor, limit = 200) {
  const result = await pool.query(
    `SELECT seq, thread_id, turn_id, watch_id, event_type, payload, created_at
     FROM duality.session_events
     WHERE thread_id = $1 AND seq > $2
     ORDER BY seq ASC
     LIMIT $3`,
    [threadId, cursor, limit]
  );
  return result.rows;
}

dualityRouter.get('/sessions/:threadId/events', async (req, res, next) => {
  let client;
  let heartbeat;
  let closed = false;
  // Serializes replay + notification handling so lastSeq is always updated
  // atomically (no interleaved double-send on coalesced notifications).
  let chain = Promise.resolve();
  let lastSeq = 0;

  try {
    const { threadId } = req.params;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(threadId)) {
      throw new BadRequestError('threadId must be a UUID');
    }
    let after = 0;
    if (req.query.after !== undefined && req.query.after !== '') {
      after = Number(req.query.after);
      if (!Number.isInteger(after) || after < 0) {
        throw new BadRequestError('after must be a non-negative integer sequence');
      }
    }

    // ── SSE response setup ──
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');
    res.flushHeaders();
    req.socket.setTimeout(0);
    res.setTimeout(0);

    // Dedicated connection for LISTEN; destroyed on close so no pooled
    // connection leaks the subscription into unrelated queries.
    client = await pool.connect();
    await client.query('LISTEN duality_session_events');

    // Current max sequence — the resume baseline for this connection.
    const maxRes = await pool.query(
      'SELECT COALESCE(MAX(seq), 0)::bigint AS seq FROM duality.session_events WHERE thread_id = $1',
      [threadId]
    );
    const connectMaxSeq = Number(maxRes.rows[0].seq);
    lastSeq = Math.max(after, 0);

    const enqueue = (fn) => {
      chain = chain.then(fn).catch((err) => {
        if (!closed) console.error('[duality-sse] push error:', err.message);
      });
    };

    // ── Live push: PG NOTIFY → fetch rows > lastSeq → write frames ──
    client.on('notification', (msg) => {
      if (closed || msg.channel !== 'duality_session_events') return;
      let payload;
      try { payload = JSON.parse(msg.payload); } catch { return; }
      if (payload.thread_id !== threadId) return;
      enqueue(async () => {
        if (closed) return;
        const rows = await fetchEventsAfter(threadId, lastSeq);
        for (const row of rows) {
          const env = eventEnvelope(row);
          sendSse(res, env.eventType, env);
          lastSeq = Math.max(lastSeq, env.seq);
        }
      });
    });

    // ── Replay backlog (seq > after), then announce connected ──
    enqueue(async () => {
      if (closed) return;
      const rows = await fetchEventsAfter(threadId, lastSeq);
      let replayed = 0;
      for (const row of rows) {
        const env = eventEnvelope(row);
        sendSse(res, env.eventType, env);
        lastSeq = Math.max(lastSeq, env.seq);
        replayed += 1;
      }
      sendSse(res, 'connected', {
        threadId,
        after: after || null,
        seq: connectMaxSeq,
        replayed,
      });
    });

    // ── Heartbeat every 15s (keeps proxies + EventSource alive) ──
    heartbeat = setInterval(() => {
      if (closed) return;
      try { sendSse(res, 'heartbeat', { seq: lastSeq }); } catch { /* socket gone */ }
    }, 15_000);

    // ── Cleanup on client disconnect / error ──
    const cleanup = () => {
      if (closed) return;
      closed = true;
      if (heartbeat) clearInterval(heartbeat);
      if (client) {
        client.removeAllListeners('notification');
        // Destroy the dedicated connection so the LISTEN subscription does
        // not linger on a pooled connection.
        client.release(true);
        client = undefined;
      }
    };
    req.on('close', cleanup);
    res.on('error', cleanup);
  } catch (err) {
    if (client) { try { client.release(true); } catch { /* noop */ } }
    next(err);
  }
});
