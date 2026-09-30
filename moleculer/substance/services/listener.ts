// ── listener.ts ──────────────────────────────────────────────────────────────
// Port of python/substance/listener.py.
//
// When a segments_history row is superseded (expiration_dt changed from the
// sentinel 9999-12-31 to now()), a DB trigger emits:
//
//   pg_notify('segment_expired', '{"segment_id":"<uuid>","segment_set_ids":[...]}')
//
// This listens for those notifications and invalidates every affected cached
// segment set so the next GET lazily rebuilds with current-valid rows.
//
// Design (carried over unchanged):
// - A dedicated pg Client (NOT from the pool) handles LISTEN — pool connections
//   are recycled and LISTEN state is per-connection.
// - Notifications are queued and drained by a bounded async queue; a saturated
//   queue drops the event rather than applying backpressure.
// - On connection loss the listener reconnects with backoff. Missed
//   notifications are tolerated — the Redis TTL on segment-set keys is a safety
//   net that bounds staleness.

import { Client } from "pg";

import { invalidateSegset } from "./cache";
import { getSettings } from "./config";

const CHANNEL = "segment_expired";
const QUEUE_MAX = 256;
const DRAIN_INTERVAL_MS = 1000;

interface SegmentExpiredPayload {
  segment_id?: string;
  segment_set_ids?: string[];
}

/** Bounded queue of raw notification payloads awaiting invalidation. */
class NotifyQueue {
  private readonly items: string[] = [];
  private dropped = 0;
  private waiter: (() => void) | null = null;

  push(payload: string): void {
    if (this.items.length >= QUEUE_MAX) {
      // Queue saturated — missed invalidation tolerated via the Redis TTL
      // safety net. Losing an invalidation is strictly better than unbounded
      // growth or blocking the socket.
      this.dropped += 1;
      return;
    }
    this.items.push(payload);
    this.wake();
  }

  shift(): string | undefined {
    return this.items.shift();
  }

  get size(): number {
    return this.items.length;
  }

  get droppedCount(): number {
    return this.dropped;
  }

  /** Resolve a pending shift() as soon as an item arrives. */
  wait(ms: number): Promise<string | undefined> {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value: string | undefined) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        this.waiter = null;
        resolve(value);
      };
      const timer = setTimeout(() => finish(undefined), ms);
      if (timer.unref) {
        timer.unref();
      }
      this.waiter = () => finish(this.items.length > 0 ? this.items.shift() : undefined);
    });
  }

  private wake(): void {
    const waiter = this.waiter;
    if (waiter) {
      waiter();
    }
  }
}

const queue = new NotifyQueue();

/** Parse a notification payload. Returns null when malformed. */
export function parseSegmentExpired(
  payload: string,
): SegmentExpiredPayload | null {
  try {
    const data: unknown = JSON.parse(payload);
    if (typeof data !== "object" || data === null || Array.isArray(data)) {
      return null;
    }
    return data as SegmentExpiredPayload;
  } catch {
    return null;
  }
}

/** Invalidate every segment set named by one notification. */
export async function handleSegmentExpired(payload: string): Promise<number> {
  const data = parseSegmentExpired(payload);
  if (data === null) {
    console.warn(`[listener] malformed payload: ${payload.slice(0, 120)}`);
    return 0;
  }
  const setIds = Array.isArray(data.segment_set_ids) ? data.segment_set_ids : [];
  const segId = data.segment_id ?? "?";
  let invalidated = 0;
  for (const ssid of setIds) {
    try {
      await invalidateSegset(String(ssid));
      invalidated += 1;
    } catch (err) {
      console.warn(
        `[listener] failed to invalidate ${ssid}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  }
  if (invalidated > 0) {
    console.log(
      `[listener] segment ${segId} expired → invalidated ${invalidated}/${setIds.length} segset(s)`,
    );
  }
  return invalidated;
}

/** Run the drain loop. Resolves when `stop` is called. */
export async function drainNotifications(stop: () => boolean): Promise<void> {
  while (!stop()) {
    const payload = (await queue.wait(DRAIN_INTERVAL_MS)) ?? queue.shift();
    if (payload === undefined) {
      continue; // heartbeat tick — loop back, check cancellation
    }
    await handleSegmentExpired(payload);
  }
}

/**
 * Long-lived task: LISTEN segment_expired, invalidate affected caches.
 * Resolves when `stop` flips to true. Reconnects automatically on connection loss.
 */
export async function listenSegmentExpirations(stop: () => boolean): Promise<void> {
  const settings = getSettings();
  let reconnectDelay = 1000;

  while (!stop()) {
    let client: Client | null = null;
    try {
      // Dedicated connection — not from the main pool.
      client = new Client({ connectionString: settings.postgresDsn });
      client.on("notification", (msg) => {
        if (msg.channel === CHANNEL) {
          queue.push(msg.payload ?? "");
        }
      });
      client.on("error", (err) => {
        // The reconnect loop below owns recovery; without a listener an emitted
        // 'error' would be an unhandled event and kill the process.
        console.warn(`[listener] client error: ${err.message}`);
      });
      await client.connect();
      await client.query(`LISTEN ${CHANNEL}`);
      reconnectDelay = 1000;
      console.log(
        `[listener] connected — LISTEN ${CHANNEL} on dedicated connection`,
      );
      await drainNotifications(stop);
    } catch (err) {
      if (stop()) {
        break;
      }
      const message = err instanceof Error ? err.message : String(err);
      console.warn(
        `[listener] connection error: ${message} — reconnecting in ${Math.round(reconnectDelay / 1000)}s`,
      );
      await new Promise((r) => setTimeout(r, reconnectDelay));
      reconnectDelay = Math.min(Math.round(reconnectDelay * 1.5), 30_000);
    } finally {
      if (client !== null) {
        try {
          await client.end();
        } catch {
          // Already gone; nothing useful to do.
        }
      }
    }
  }
}

/** Test hook: inspect the queue. */
export function queueStats(): { size: number; dropped: number } {
  return { size: queue.size, dropped: queue.droppedCount };
}
