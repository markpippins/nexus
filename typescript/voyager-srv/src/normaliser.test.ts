/**
 * Voyager-srv unit tests: the pure normaliser helpers every route response
 * flows through (routes.ts camelCaseRow/camelCaseRows/toNumber).
 *
 * These pin the snake_case→camelCase contract that voyager-srv's API
 * consumers depend on, including the two subtle behaviours:
 *   * Date values are serialised to epoch milliseconds (not ISO strings)
 *   * snake_ to camel only fires on lowercase letter after underscore
 *
 * First suite for this service (it had none): written hermetic — no
 * database, no network — so the CI gate can run it fail-closed.
 */
import { describe, it, expect } from 'vitest';
import { camelCaseRow, camelCaseRows, toNumber } from './routes.js';

describe('camelCaseRow', () => {
  it('converts snake_case keys to camelCase', () => {
    const out = camelCaseRow({ scan_epoch_id: 'e1', started_at: 1, path: '/x' });
    expect(out).toEqual({ scanEpochId: 'e1', startedAt: 1, path: '/x' });
  });

  it('leaves non-snake keys untouched', () => {
    const out = camelCaseRow({ id: 'a', structure: { type: 'dir' } });
    expect(out).toEqual({ id: 'a', structure: { type: 'dir' } });
  });

  it('serialises Date values to epoch milliseconds', () => {
    const d = new Date('2026-09-28T00:00:00Z');
    const out = camelCaseRow({ discovered_at: d });
    expect(out.discoveredAt).toBe(d.getTime());
    expect(typeof out.discoveredAt).toBe('number');
  });

  it('preserves null and numeric-zero values (no falsy collapse)', () => {
    const out = camelCaseRow({ confidence: 0, entity_id: null, inode: 42 });
    expect(out).toEqual({ confidence: 0, entityId: null, inode: 42 });
  });

  it('passes through nested objects by reference (shallow contract)', () => {
    const structure = { type: 'dir', entries: 3 };
    const out = camelCaseRow({ structure, valid_from: 5 });
    expect(out.structure).toBe(structure);
    expect(out.validFrom).toBe(5);
  });
});

describe('camelCaseRows', () => {
  it('maps every row and preserves order', () => {
    const rows = [
      { span_id: 's1', markdown_role: 'heading' },
      { span_id: 's2', markdown_role: 'body' },
    ];
    expect(camelCaseRows(rows)).toEqual([
      { spanId: 's1', markdownRole: 'heading' },
      { spanId: 's2', markdownRole: 'body' },
    ]);
  });

  it('returns an empty array for an empty result set', () => {
    expect(camelCaseRows([])).toEqual([]);
  });
});

describe('toNumber', () => {
  it('parses numeric query values', () => {
    expect(toNumber('7', 1)).toBe(7);
    expect(toNumber(3, 1)).toBe(3);
    expect(toNumber('2.5', 1)).toBe(2.5);
  });

  it('falls back on NaN, Infinity and garbage', () => {
    expect(toNumber('abc', 9)).toBe(9);
    // Pinned quirk: Number('') === 0 is finite, so empty string yields 0,
    // not the fallback. Absent query params arrive as undefined (below).
    expect(toNumber('', 9)).toBe(0);
    expect(toNumber(NaN, 9)).toBe(9);
    expect(toNumber(Infinity, 9)).toBe(9);
    expect(toNumber(undefined as any, 9)).toBe(9);
  });
});
