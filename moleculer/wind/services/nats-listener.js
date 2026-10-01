// ── NATS Listener ───────────────────────────────────────────────────
//
// Subscribes to `nexus.wind.v1.events.>` for real-time event processing.
// When an event arrives via NATS:
//   1. Check if it's already in wind.events (by dedup)
//   2. If not, insert it into wind.events
//   3. The recovery poller will pick it up (or we can process immediately)
//
// Also exports `publishToNats()` for use by REST handlers to broadcast
// events in real-time.

import { pool } from './db.js';
import { config } from './config.js';

let nc = null;
let sub = null;
let cleanupHandlers = [];

// ── NATS connection (lazy singleton) ────────────────────────────────

async function getNatsConnection() {
  if (nc) return nc;
  try {
    const { connect } = await import('nats');
    nc = await connect({ servers: config.natsUrl, name: 'wind-srv' });
    console.log('[nats-listener] Connected to NATS at', config.natsUrl);
    return nc;
  } catch (err) {
    console.warn('[nats-listener] Cannot connect to NATS:', err.message);
    return null;
  }
}

// ── Publish ─────────────────────────────────────────────────────────

/**
 * Publish an event payload to a NATS subject.
 * Falls back to no-op logging if NATS is unavailable.
 */
export async function publishToNats(subject, payload) {
  try {
    const conn = await getNatsConnection();
    if (!conn) {
      console.log(`[nats-listener] [STUB] ${subject}:`, JSON.stringify(payload).slice(0, 200));
      return false;
    }
    const { JSONCodec } = await import('nats');
    const jc = JSONCodec();
    conn.publish(subject, jc.encode(payload));
    await conn.flush();
    return true;
  } catch (err) {
    console.warn('[nats-listener] Publish error:', err.message);
    return false;
  }
}

// ── Event handler ───────────────────────────────────────────────────

/**
 * Handle an incoming NATS event.
 * Inserts into wind.events if not already present (idempotent via dedup),
 * then logs. The recovery event-processor will pick it up.
 */
async function handleIncomingEvent(subject, payload) {
  const { event_id, event_type, source, payload: eventPayload } = payload;

  if (!event_type) {
    console.warn('[nats-listener] Received event without event_type:', subject);
    return;
  }

  // Check if event already exists (dedup by event_id if provided)
  if (event_id) {
    const existing = await pool.query(
      'SELECT id FROM wind.events WHERE id = $1',
      [event_id]
    );
    if (existing.rows.length > 0) {
      return; // already processed
    }
  }

  // Insert into wind.events
  const result = await pool.query(
    `INSERT INTO wind.events (event_type, subject, payload, source)
     VALUES ($1, $2, $3::jsonb, $4)
     ON CONFLICT DO NOTHING
     RETURNING id`,
    [event_type, subject, JSON.stringify(eventPayload || payload), source || 'nats']
  );

  if (result.rows.length > 0) {
    console.log(`[nats-listener] Ingested ${event_type} → event ${result.rows[0].id}`);
  }
}

// ── Public API ──────────────────────────────────────────────────────

/**
 * Start the NATS listener subscription.
 * Subscribes to `nexus.wind.v1.events.>` — all wind events.
 * Returns a cleanup function.
 */
export async function startNatsListener() {
  try {
    const conn = await getNatsConnection();
    if (!conn) {
      console.warn('[nats-listener] NATS unavailable — real-time path disabled');
      return () => stopNatsListener();
    }

    const { JSONCodec } = await import('nats');
    const jc = JSONCodec();

    sub = conn.subscribe('nexus.wind.v1.events.>');
    console.log('[nats-listener] Subscribed to nexus.wind.v1.events.>');

    (async () => {
      for await (const msg of sub) {
        try {
          const payload = jc.decode(msg.data);
          await handleIncomingEvent(msg.subject, payload);
        } catch (err) {
          console.warn('[nats-listener] Error handling message:', err.message);
        }
      }
    })();

    return () => stopNatsListener();
  } catch (err) {
    console.warn('[nats-listener] Failed to start:', err.message);
    return () => {};
  }
}

/**
 * Stop the NATS listener and close connection.
 */
export async function stopNatsListener() {
  if (sub) {
    sub.unsubscribe();
    sub = null;
  }
  if (nc) {
    await nc.close();
    nc = null;
    console.log('[nats-listener] Disconnected');
  }
}
