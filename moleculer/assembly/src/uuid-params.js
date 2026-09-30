/**
 * UUID path-param guard for assembly-srv routes.
 *
 * House context: agents (and humans) habitually address threads by 8-char id
 * prefixes. Path params are handed to pg as query args, where a prefix fails
 * the `uuid` cast with 22P02 (invalid_text_representation) and surfaces as an
 * opaque HTTP 500. This module converts that failure into a 400 with a
 * repairable message at the boundary.
 *
 * Incident: 2026-09-20 (record 60f90514) — POST /threads/ef6c04df/comments
 * 500'd with 22P02; identical probe with the full UUID returned 201. The
 * endpoint was never broken; the shorthand was.
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

import { BadRequestError } from './errors.js';

/**
 * Validate a path/body id that will reach PostgreSQL as a uuid.
 * Returns the normalized (lowercase) UUID or throws BadRequestError.
 *
 * @param {string | undefined} value raw param value
 * @param {string} name param name, for the error message
 * @returns {string} the full, lowercase UUID
 */
export function requireUuid(value, name = 'id') {
  if (typeof value !== 'string' || !UUID_RE.test(value)) {
    const shown = typeof value === 'string' ? value.slice(0, 16) : String(value);
    throw new BadRequestError(
      `${name} must be a full UUID (got "${shown}") — resolve the full id from the thread-list endpoints first`,
    );
  }
  return value.toLowerCase();
}

export default requireUuid;
