import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';
import { publishToNats } from '../nats-listener.js';

export const eventsRouter = Router();

// ── List events (with optional filters) ─────────────────────────────

eventsRouter.get('/', async (req, res, next) => {
  try {
    const { event_type, consumed, limit = 50 } = req.query;
    let sql = `SELECT id, event_type, subject, payload, source, created_at, consumed_at, metadata
               FROM wind.events WHERE 1=1`;
    const params = [];
    let idx = 1;

    if (event_type) {
      sql += ` AND event_type = $${idx++}`;
      params.push(event_type);
    }
    if (consumed === 'false') {
      sql += ' AND consumed_at IS NULL';
    } else if (consumed === 'true') {
      sql += ' AND consumed_at IS NOT NULL';
    }

    sql += ' ORDER BY created_at DESC LIMIT $' + idx;
    params.push(parseInt(String(limit), 10) || 50);

    const result = await query(sql, params);
    res.json(result.rows);
  } catch (err) { next(err); }
});

// ── Get single event ───────────────────────────────────────────────

eventsRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await query(
      'SELECT id, event_type, subject, payload, source, created_at, consumed_at, metadata FROM wind.events WHERE id = $1',
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Event not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// ── Create event ────────────────────────────────────────────────────

eventsRouter.post('/', async (req, res, next) => {
  try {
    const { event_type, subject, payload, source } = req.body;
    if (!event_type) throw new BadRequestError('event_type is required');

    const result = await query(
      `INSERT INTO wind.events (event_type, subject, payload, source)
       VALUES ($1, $2, $3::jsonb, $4)
       RETURNING id, event_type, subject, payload, source, created_at`,
      [event_type, subject || `nexus.wind.v1.events.${event_type.replace(/\./g, '.')}`, JSON.stringify(payload || {}), source || 'api']
    );
    const event = result.rows[0];

    // Broadcast to NATS for real-time subscribers
    await publishToNats(event.subject, {
      event_id: event.id,
      event_type: event.event_type,
      source: event.source,
      payload: event.payload,
    });

    res.status(201).json(event);
  } catch (err) { next(err); }
});

// ── Poll unconsumed events (FOR UPDATE SKIP LOCKED) ────────────────

eventsRouter.post('/poll', async (req, res, next) => {
  try {
    const { limit = 10 } = req.body;
    const client = await (await import('../db.js')).pool.connect();
    try {
      const result = await client.query(
        `SELECT id, event_type, subject, payload, source, created_at
         FROM wind.events
         WHERE consumed_at IS NULL
         ORDER BY created_at ASC
         LIMIT $1
         FOR UPDATE SKIP LOCKED`,
        [Math.min(parseInt(String(limit), 10) || 10, 100)]
      );

      res.json(result.rows);
    } finally {
      client.release();
    }
  } catch (err) { next(err); }
});
