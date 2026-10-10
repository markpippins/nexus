// ── Rover Scheduler ─────────────────────────────────────────────────
//
// Runs a background timer that periodically checks for unprocessed
// harvests in the nebula schema. For each unprocessed harvest, it
// creates a `harvest.created` event in wind.events and publishes to
// NATS, triggering the Rover Stage 2 pipeline.
//
// The timer fires immediately on start, then every 30 minutes.

import pg from 'pg';
import { config } from './config.js';

const { Pool } = pg;
const pool = new Pool({ connectionString: config.pgDsn, max: 2 });

let schedulerInterval = null;

// ── NATS publish helper ─────────────────────────────────────────────

async function publishToNats(subject, payload) {
  try {
    const { connect, JSONCodec } = await import('nats');
    const nc = await connect({ servers: config.natsUrl });
    const jc = JSONCodec();
    nc.publish(subject, jc.encode(payload));
    await nc.flush();
    await nc.close();
    return true;
  } catch (err) {
    console.warn(`[scheduler] NATS unavailable: ${err.message}`);
    return false;
  }
}

// ── Scheduler tick ──────────────────────────────────────────────────

/**
 * One scheduler tick: find oldest unprocessed harvest, create event.
 */
async function schedulerTick() {
  const client = await pool.connect();
  try {
    // Find the oldest harvest that does NOT already have a wind event
    const result = await client.query(`
      SELECT h.id, h.created_at
      FROM nebula.harvests h
      WHERE NOT EXISTS (
        SELECT 1 FROM wind.events e
        WHERE e.event_type = 'harvest.created'
          AND e.payload->>'harvest_id' = h.id::text
      )
      ORDER BY h.created_at ASC
      LIMIT 1
    `);

    if (result.rows.length === 0) {
      return; // no unprocessed harvests
    }

    const harvest = result.rows[0];

    // Create the wind event
    const eventResult = await client.query(`
      INSERT INTO wind.events (event_type, subject, payload, source)
      VALUES ('harvest.created', 'nexus.wind.v1.events.harvest.created',
              $1::jsonb, 'wind-srv/scheduler')
      RETURNING id
    `, [JSON.stringify({ harvest_id: harvest.id, harvest_created_at: harvest.created_at.toISOString() })]);

    const eventId = eventResult.rows[0].id;
    console.log(`[scheduler] Created harvest.created event ${eventId} for harvest ${harvest.id}`);

    // Publish to NATS for real-time processing
    await publishToNats('nexus.wind.v1.events.harvest.created', {
      event_id: eventId,
      event_type: 'harvest.created',
      source: 'wind-srv/scheduler',
      payload: { harvest_id: harvest.id, harvest_created_at: harvest.created_at.toISOString() },
    });

  } catch (err) {
    console.error('[scheduler] Tick error:', err.message);
  } finally {
    client.release();
  }
}

// ── Public API ──────────────────────────────────────────────────────

/**
 * Start the Rover scheduler.
 * Fires immediately, then every config.roverSchedulerIntervalMs.
 * Returns a cleanup function.
 */
export function startRoverScheduler() {
  if (schedulerInterval) {
    console.warn('[scheduler] Already running');
    return () => stopRoverScheduler();
  }

  // Fire immediately
  schedulerTick();

  // Then repeat
  schedulerInterval = setInterval(schedulerTick, config.roverSchedulerIntervalMs);
  console.log(`[scheduler] Started (every ${config.roverSchedulerIntervalMs / 60000} min)`);

  return () => stopRoverScheduler();
}

/**
 * Stop the Rover scheduler.
 */
export function stopRoverScheduler() {
  if (schedulerInterval) {
    clearInterval(schedulerInterval);
    schedulerInterval = null;
    console.log('[scheduler] Stopped');
  }
}
