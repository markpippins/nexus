// src/substance-proxy.js — Thin HTTP client for routing segment-set reads to
// substance (:3115).
//
// Why this exists: substance owns the segment-set scheme (nebula.segment_sets
// and its domain join tables) including its Redis read-through cache and
// LISTEN/NOTIFY invalidation. Assembly must NOT query those tables directly —
// going around substance would (a) duplicate the resolution logic and (b)
// read stale data the cache discipline exists to prevent. Per the same
// doctrine that moved nebula reads behind nebula-proxy (Assembly Rewrite
// thread, 2026-07-24), segment-set reads route through substance.
//
// READ-ONLY by design: this proxy intentionally exposes no write helpers.
// Segment sets are created/linked by the ingest tooling through substance
// itself; assembly-srv surfaces evidence for deliberation only.
//
// Substance returns plain JSON (not the nebula-srv Paged<T> envelope), so
// responses are passed through after a shape check; 404s map to NotFoundError.

import { AppError, NotFoundError } from './errors.js';

export const SUBSTANCE_BASE =
  process.env.SUBSTANCE_BASE_URL || 'http://localhost:3115';

/**
 * Forward a request to substance.
 *
 * @param {string} path   Absolute path on substance (e.g.
 *                        "/candidates/<id>/segment-sets").
 * @param {object} [opts]
 * @param {string} [opts.method]  HTTP method (default "GET").
 * @returns {Promise<any>} Parsed JSON response body.
 * @throws {AppError} 4xx/5xx responses are re-thrown as AppError with
 *                    matching status code and message.
 */
export async function fetchSubstance(path, opts = {}) {
  const { method = 'GET' } = opts;
  let res;
  try {
    res = await fetch(`${SUBSTANCE_BASE}${path}`, {
      method,
      headers: { accept: 'application/json' },
      signal: AbortSignal.timeout(10_000),
    });
  } catch (err) {
    throw new AppError(
      `substance unreachable at ${SUBSTANCE_BASE}: ${err.message}`,
      502,
    );
  }
  if (res.status === 404) {
    throw new NotFoundError('Not found');
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new AppError(`substance ${res.status}: ${body.slice(0, 300)}`, res.status);
  }
  return res.json();
}

/**
 * Normalize substance's snake_case resolved-segment-set shape to the
 * camelCase assembly-srv convention.
 */
export function substanceToCamel(obj) {
  if (Array.isArray(obj)) return obj.map(substanceToCamel);
  if (obj && typeof obj === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(obj)) {
      out[k.replace(/_([a-z])/g, (_, c) => c.toUpperCase())] =
        v && typeof v === 'object' ? substanceToCamel(v) : v;
    }
    return out;
  }
  return obj;
}
