import { describe, it, expect } from 'vitest';
import { requireUuid, default as requireUuidDefault } from './uuid-params.js';
import { BadRequestError } from './errors.js';

describe('requireUuid (incident 2026-09-20: prefixed ids 500ing with 22P02)', () => {
  const FULL = 'ef6c04df-af36-4098-940c-12ebbc0004d3';

  it('accepts a full UUID and returns it lowercase', () => {
    expect(requireUuid(FULL, 'threadId')).toBe(FULL);
    expect(requireUuid(FULL.toUpperCase(), 'threadId')).toBe(FULL);
  });

  it('rejects an 8-char prefix with a 400-class BadRequestError (was 500/22P02)', () => {
    try {
      requireUuid('ef6c04df', 'threadId');
      expect.unreachable('should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(BadRequestError);
      expect(err.statusCode).toBe(400);
      expect(String(err.message)).toContain('full UUID');
      expect(String(err.message)).toContain('ef6c04df');
    }
  });

  it('rejects other malformed ids (empty, numeric, near-miss uuid)', () => {
    for (const bad of ['', '123', 'ef6c04df-af36-4098', 'xxxxxxxx-af36-4098-940c-12ebbc0004d3']) {
      expect(() => requireUuid(bad, 'id')).toThrow(BadRequestError);
    }
  });

  it('exports the same function as the default export', () => {
    expect(requireUuidDefault).toBe(requireUuid);
  });
});
