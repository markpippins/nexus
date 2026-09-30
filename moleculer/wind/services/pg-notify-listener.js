// ── PG Notification Listener ───────────────────────────────────────────
//
// Listens for 'wind_event_bridge' notifications from the bridge trigger
// (wind.trg_bridge_conduit_events). When a WRP event is inserted into
// wind.events via PG trigger, catches the pg_notify and publishes to NATS
// for real-time subscribers.
//
// Without this, WRP events bridged via PG would only be picked up by the
// recovery poller (every 5s) and never reach NATS.

import { pool } from './db.js';
import { publishToNats } from './nats-listener.js';

let client = null;
let stopped = false;

/**
 * Start listening for PG notifications on wind_event_bridge.
 * Returns a cleanup function (best-effort — unlistens and releases).
 */
export async function startPgNotifyListener() {
  if (client) {
    console.log('[pg-notify-listener] Already listening');
    return () => stopPgNotifyListener();
  }

  try {
    client = await pool.connect();
    await client.query('LISTEN wind_event_bridge');
    console.log('[pg-notify-listener] Listening on wind_event_bridge');

    client.on('notification', async (msg) => {
      if (stopped || !msg.payload) return;
      try {
        const data = JSON.parse(msg.payload);
        const { event_id, event_type, subject, source, payload } = data;
        if (!subject || !event_type) return;

        const published = await publishToNats(subject, {
          event_id,
          event_type,
          source: source || 'conduit-runtime',
          payload: payload || {},
        });

        if (published) {
          console.log(`[pg-notify-listener] Published ${event_type} → NATS`);
        }
      } catch (err) {
        console.warn('[pg-notify-listener] Notification error:', err.message);
      }
    });

    client.on('error', (err) => {
      console.error('[pg-notify-listener] PG error:', err.message);
      client = null;
    });

    client.on('end', () => {
      console.log('[pg-notify-listener] PG connection ended');
      client = null;
    });
  } catch (err) {
    console.error('[pg-notify-listener] Failed to start:', err.message);
    client = null;
  }

  return () => stopPgNotifyListener();
}

export async function stopPgNotifyListener() {
  stopped = true;
  if (client) {
    try { await client.query('UNLISTEN wind_event_bridge'); } catch (_) {}
    try { client.release(); } catch (_) {}
    client = null;
  }
}
