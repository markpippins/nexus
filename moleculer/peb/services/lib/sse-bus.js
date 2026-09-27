import { EventEmitter } from 'node:events';

// Minimal in-process SSE fan-out bus. Persisted across SSE connections within
// a single server process. Lists of subscribers get notified whenever
// governance_events are written (via REST POST /events/{receipt_id}/replay)
// or whenever the POLLER sees a row newer than the last-seen cursor on its
// next poll cycle.
//
// For multi-process / HA deployments, swap push() to publish into Redis pub/sub
// or Postgres LISTEN/NOTIFY; the subscriber protocol stays the same.

export const PEB_EVENTS = 'peb:event';

class SseBus extends EventEmitter {
  constructor() {
    super();
    this.setMaxListeners(50);
  }

  push(eventType, payload) {
    this.emit(PEB_EVENTS, {
      type: eventType,
      payload,
      emitted_at: new Date().toISOString(),
    });
  }
}

export const sseBus = new SseBus();

// Convert an SSE-resident event object into the wire format
// `event: <type>\ndata: <json>\n\n` per the SSE spec.
export function formatSseMessage(event) {
  const data = JSON.stringify(event.payload ?? {});
  return `event: ${event.type}\ndata: ${data}\n\n`;
}
