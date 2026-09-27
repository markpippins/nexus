import { Router } from 'express';
import { pool } from '../db.js';
import { sseBus, PEB_EVENTS, formatSseMessage } from '../lib/sse-bus.js';

export const streamRouter = Router();

// GET /api/peb/events/stream?plan_id=&agent_role=
//
// Opens an SSE channel and pushes governance_events newer than the latest
// event id at connect time, plus any subsequent events emitted by the
// in-process SSE bus (e.g. replay events). Optional filters limit the
// stream to plan_id / agent_role.
//
// Implementation note: we use a poll loop (1s) against the DB to surface new
// governance_events rows. A more elegant path is PG LISTEN/NOTIFY against
// a trigger on peb.governance_events after INSERT; we keep that as a TODO
// on the README and lean on the simpler poller for now.
streamRouter.get('/', async (req, res) => {
  // SSE headers
  res.writeHead(200, {
    'Content-Type':  'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection:      'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.write(`event: ready\ndata: ${JSON.stringify({ connected: true })}\n\n`);

  const planId  = req.query.plan_id  ? String(req.query.plan_id)  : null;
  const agentRole = req.query.agent_role ? String(req.query.agent_role) : null;

  // Start cursor: latest governance_events.id present at connect time.
  let cursor = 0;
  try {
    const r = await pool.query('SELECT coalesce(max(id),0)::bigint AS max_id FROM peb.governance_events');
    cursor = Number(r.rows[0].max_id);
  } catch (e) {
    res.write(`event: error\ndata: ${JSON.stringify({ stage: 'init', message: e.message })}\n\n`);
  }

  // Wire-up to the in-process SSE bus (fires on replays and out-of-band
  // pushes). Filter on plan_id and agent_role before emitting.
  const onEvent = (event) => {
    const p = event.payload || {};
    if (planId && p.plan_id && p.plan_id !== planId) return;
    if (agentRole && p.agent_role && p.agent_role !== agentRole) return;
    res.write(formatSseMessage(event));
  };
  sseBus.on(PEB_EVENTS, onEvent);

  // Heartbeat + poll loop. Closes when client disconnects.
  const keepalive = setInterval(() => {
    res.write(`: keepalive ${Date.now()}\n\n`);
  }, 15000);

  const poller = setInterval(async () => {
    try {
      const args = [];
      const where = ['ge.id > $1'];
      args.push(cursor);
      let n = 2;
      if (planId)    { where.push(`ge.plan_id = $${n++}`);     args.push(planId); }
      if (agentRole) { where.push(`ge.agent_role = $${n++}`);  args.push(agentRole); }
      const r = await pool.query(
        `SELECT ge.id, ge.receipt_id, ge.event_type, ge.work_request_id,
                ge.plan_id, ge.agent_role, ge.payload, ge.created_at, ge.replayed_at
           FROM peb.governance_events ge
          WHERE ${where.join(' AND ')}
          ORDER BY ge.id ASC
          LIMIT 100`,
        args
      );
      for (const row of r.rows) {
        res.write(formatSseMessage({
          type: row.event_type || 'event',
          payload: row,
        }));
        cursor = Number(row.id);
      }
    } catch (e) {
      res.write(`event: error\ndata: ${JSON.stringify({ stage: 'poll', message: e.message })}\n\n`);
    }
  }, 1000);

  // Cleanup
  req.on('close', () => {
    clearInterval(keepalive);
    clearInterval(poller);
    sseBus.off(PEB_EVENTS, onEvent);
    res.end();
  });
});
