import { badRequest } from '../errors.js';

// Cursor pagination helper for the event stream. We treat:
//   - `since` as an exclusive lower-bound bigint cursor on
//     governance_events.id (monotonic serial bigserial).
//   - `limit` capped to [1, 500] default 100.
//   - `offset` >= 0; allowed in addition to `since` for ad-hoc debugging.
export function parseEventCursor(query) {
  const limit = clampLimit(query?.limit);
  const offset = clampOffset(query?.offset);

  let since = null;
  if (query?.since != null && query.since !== '') {
    const n = Number(query.since);
    if (!Number.isInteger(n) || n < 0) {
      throw badRequest('since must be a non-negative integer (governance_events.id cursor)');
    }
    since = n;
  }
  return { limit, offset, since };
}

export function clampLimit(v) {
  if (v == null || v === '') return 100;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 1) {
    throw badRequest('limit must be a positive integer');
  }
  if (n > 500) return 500;
  return n;
}

export function clampOffset(v) {
  if (v == null || v === '') return 0;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 0) {
    throw badRequest('offset must be a non-negative integer');
  }
  return n;
}

// Parse a time window from `?window=24h` / `?window=1d` / `?window=45m`
// returns a Date object representing the lower bound (now - window).
export function parseTimeWindow(v) {
  if (v == null || v === '') return null;
  const m = /^(\d+)([hdm])$/.exec(String(v));
  if (!m) {
    throw badRequest("window must match N<h|d|m>, e.g. 24h, 1d, 45m");
  }
  const n = Number(m[1]);
  if (!Number.isFinite(n) || n <= 0) {
    throw badRequest("window length must be positive");
  }
  const ms = { h: 3600_000, d: 86_400_000, m: 60_000 }[m[2]];
  return new Date(Date.now() - n * ms);
}

// Validate a UUID-style path parameter. The peb schema uses uuid for
// transactions.decisions.traces.violations.state, and free-form varchar for
// governance_events.receipt_id and entities.entity_id. We don't enforce UUID
// strictly here — we only refuse obviously bad shape.
export function isAcceptableId(s) {
  if (typeof s !== 'string' || s.length === 0 || s.length > 256) return false;
  // Allow UUID, print-safe text. Reject anything with control chars or
  // quotes/feints.
  if (!/^[\x21-\x7E]+$/.test(s)) return false;
  return !/['"\\]/.test(s);
}
