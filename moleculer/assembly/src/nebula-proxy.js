// src/nebula-proxy.js — Thin HTTP client for routing nebula-domain reads to nebula-srv.
//
// Why this exists: assembly-srv is the deliberation/social layer over nebula domain
// objects. It used to inline `pool.query('SELECT … FROM nebula.<table>')` everywhere,
// duplicating data access that nebula-srv exposes via REST. Per the "Assembly is a
// social/deliberation site that lets us discuss, deliberate and plan over nebula
// domain objects — and assembly should call nebula-srv wherever possible" doctrine
// (Assembly Rewrite thread, To Do forum, 2026-07-24), all such inline reads move
// through this proxy.
//
// Assembly-srv's response shape for nebula-origin routes stays Paged<T> (i.e.,
// { items, total, page, pageSize }) with camelCase fields. As of 2026-07-24
// nebula-srv's matching endpoints emit that same `{items, total, page, pageSize}`
// envelope with camelCase fields directly (see inspections cross-checked
// against `nexus/typescript/nebula-srv/src/routes.ts` and
// `nexus/typescript/nebula-srv/API-SPEC.md`), so most shim routes can pass the
// response through unchanged after a single envelope check.
//
// This module is intentionally tiny: a `fetchNebula()` that calls into
// nebula-srv, raises AppError on 4xx/5xx, and returns the parsed JSON body.
// Path remapping (e.g. /api/candidates → /api/harvest-candidates) lives in the
// route files that consume the helper — this keeps the proxy generic.

import { AppError, BadRequestError, NotFoundError } from './errors.js';

export const NEBULA_SRV_BASE =
  process.env.NEBULA_SRV_BASE_URL || 'http://localhost:3101';

/**
 * Forward a request to nebula-srv.
 *
 * @param {string} path    Absolute path on nebula-srv (e.g. "/api/agent-records").
 *                        Caller is responsible for any path remap (e.g.
 *                        `/api/candidates` → `/api/harvest-candidates`).
 * @param {object} [opts]
 * @param {string} [opts.method]      HTTP method (default "GET").
 * @param {object} [opts.query]       Query-string params (object; values stringified).
 * @param {object} [opts.body]        JSON body (for POST/PATCH/PUT).
 * @param {string[]} [opts.forward]   Headers (case-insensitive names) to forward
 *                                     from `req` to the upstream call. Defaults
 *                                     to none — most assembly shim routes need no
 *                                     headers crossing the boundary.
 * @param {import('express').Request} [opts.req] Express req, used only when
 *                                     `opts.forward` is non-empty.
 * @returns {Promise<any>} Parsed JSON response body.
 * @throws {AppError} 4xx/5xx responses from nebula-srv are re-thrown as
 *                    AppError with matching status code and message.
 */
export async function fetchNebula(path, opts = {}) {
  const {
    method = 'GET',
    query,
    body,
    forward = [],
    req,
  } = opts;

  // Build URL with query string
  const url = new URL(path, NEBULA_SRV_BASE);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      // Arrays: repeat the key (this is what Express's querystring parser
      // collapses into an array on the receiving side).
      if (Array.isArray(value)) {
        for (const v of value) url.searchParams.append(key, String(v));
      } else {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const headers = { 'Accept': 'application/json' };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }
  if (req && forward.length > 0) {
    const incoming = req.headers || {};
    for (const name of forward) {
      const v = incoming[name.toLowerCase()];
      if (v !== undefined) headers[name] = v;
    }
  }

  let upstream;
  try {
    upstream = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    // Network/transport error: 503 from assembly vantage.
    throw new AppError(
      `nebula-srv unreachable at ${url.toString()}: ${err.message}`,
      503,
    );
  }

  const text = await upstream.text();
  let parsed = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!upstream.ok) {
    const message =
      (parsed && typeof parsed === 'object' && (parsed.error || parsed.message)) ||
      `nebula-srv ${upstream.status} for ${method} ${path}`;
    // Map common upstream status codes to AppError subclasses for cleaner
    // responses through the errorHandler middleware.
    if (upstream.status === 400) throw new BadRequestError(message);
    if (upstream.status === 404) throw new NotFoundError(message);
    throw new AppError(message, upstream.status);
  }

  return parsed;
}

/**
 * Wrap a shim handler that proxies a single request to nebula-srv and
 * returns nebula's parsed JSON body directly. Used by GET-by-id and
 * GET-list routes whose response shapes now match assembly's existing
 * contract (post the 2026-07-24 nebula-srv restart).
 *
 * Caller is responsible for the path remap if any.
 *
 * @param {(req: import('express').Request) => Promise<{ path: string, query?: object }>} resolver
 * @returns Express route handler.
 */
export function proxyGet(resolver) {
  return async (req, res, next) => {
    try {
      const { path, query } = await resolver(req);
      const data = await fetchNebula(path, { method: 'GET', query });
      res.json(data);
    } catch (err) {
      next(err);
    }
  };
}
