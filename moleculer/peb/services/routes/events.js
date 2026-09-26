import { Router } from 'express';
import { pool } from '../db.js';
import { parseEventCursor, clampLimit, isAcceptableId } from '../lib/pagination.js';
import { sseBus } from '../lib/sse-bus.js';
import { badRequest, notFound } from '../errors.js';

export const eventsRouter = Router();

// GET /api/peb/events?since=<cursor>&event_type=&plan_id=&agent_role=&limit=&offset=
eventsRouter.get('/', async (req, res, next) => {
  try {
    const { limit, offset, since } = parseEventCursor(req.query);
    const args = [];
    const where = [];
    let n = 1;

    if (since != null) {
      where.push(`ge.id > $${n++}`);
      args.push(since);
    }
    if (req.query.event_type) {
      where.push(`ge.event_type = $${n++}`);
      args.push(String(req.query.event_type));
    }
    if (req.query.plan_id) {
      where.push(`ge.plan_id = $${n++}`);
      args.push(String(req.query.plan_id));
    }
    if (req.query.agent_role) {
      where.push(`ge.agent_role = $${n++}`);
      args.push(String(req.query.agent_role));
    }
    if (req.query.work_request_id) {
      where.push(`ge.work_request_id = $${n++}`);
      args.push(String(req.query.work_request_id));
    }
    args.push(limit, offset);

    const q = `
      SELECT ge.id, ge.receipt_id, ge.event_type, ge.work_request_id,
             ge.plan_id, ge.agent_role, ge.payload, ge.created_at, ge.replayed_at
      FROM peb.governance_events ge
      ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
      ORDER BY ge.id ASC
      LIMIT $${n++} OFFSET $${n++}
    `;
    const r = await pool.query(q, args);
    // Surface a `next_cursor` for callers; null if the page was < limit.
    const nextCursor = r.rows.length < limit ? null : r.rows[r.rows.length - 1].id;
    res.json({ events: r.rows, next_cursor: nextCursor, limit, offset });
  } catch (err) {
    next(err);
  }
});

// GET /api/peb/events/{receipt_id}
eventsRouter.get('/:receipt_id', async (req, res, next) => {
  try {
    const rid = req.params.receipt_id;
    if (!isAcceptableId(rid)) return next(badRequest('invalid receipt_id'));
    const r = await pool.query(
      `SELECT ge.id, ge.receipt_id, ge.event_type, ge.work_request_id,
              ge.plan_id, ge.agent_role, ge.payload, ge.created_at, ge.replayed_at
       FROM peb.governance_events ge
       WHERE ge.receipt_id = $1
       ORDER BY ge.id DESC
       LIMIT 1`,
      [rid]
    );
    if (r.rowCount === 0) return next(notFound('event not found'));
    res.json({ event: r.rows[0] });
  } catch (err) {
    next(err);
  }
});

// POST /api/peb/events/{receipt_id}/replay
// Spec: sets replayed_at, re-runs downstream effects.
// Implemented as: stamp replayed_at = now() (idempotent if already set), then
// publish a 'replay' event on /events/stream for any subscribers.
eventsRouter.post('/:receipt_id/replay', async (req, res, next) => {
  try {
    const rid = req.params.receipt_id;
    if (!isAcceptableId(rid)) return next(badRequest('invalid receipt_id'));
    const r = await pool.query(
      `UPDATE peb.governance_events
          SET replayed_at = now()
        WHERE receipt_id = $1
        RETURNING id, receipt_id, event_type, work_request_id, plan_id,
                  agent_role, payload, created_at, replayed_at`,
      [rid]
    );
    if (r.rowCount === 0) return next(notFound('event not found'));
    const ev = r.rows[0];
    sseBus.push('replay', { receipt_id: ev.receipt_id, plan_id: ev.plan_id,
                            agent_role: ev.agent_role, replayed_at: ev.replayed_at });
    res.json({ replayed: ev });
  } catch (err) {
    next(err);
  }
});
