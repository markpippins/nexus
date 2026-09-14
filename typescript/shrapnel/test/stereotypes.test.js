import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { intParam, stringArray, mapPgError } from '../src/routes/stereotypes.js';
import { ApiError } from '../src/errors.js';

describe('stereotypes router helpers', () => {
  describe('intParam', () => {
    it('accepts integers', () => {
      assert.equal(intParam('42', 'id'), 42);
      assert.equal(intParam(7, 'id'), 7);
    });
    it('rejects non-integers with 400 ApiError', () => {
      for (const bad of ['abc', '1.5', '', null, undefined]) {
        assert.throws(() => intParam(bad, 'id'), (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 400);
          assert.match(err.message, /id must be an integer/);
          return true;
        });
      }
    });
  });

  describe('stringArray', () => {
    it('returns null for undefined/null (absent)', () => {
      assert.equal(stringArray(undefined, 'x'), null);
      assert.equal(stringArray(null, 'x'), null);
    });
    it('passes through trimmed strings', () => {
      assert.deepEqual(stringArray(['a', ' b '], 'x'), ['a', 'b']);
    });
    it('rejects non-arrays and empty strings with 400', () => {
      for (const bad of [['a', ''], ['a', 42], 'not-an-array', ['']]) {
        assert.throws(() => stringArray(bad, 'x'), (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 400);
          return true;
        });
      }
    });
  });

  describe('mapPgError', () => {
    it('maps 23514 check_violation to 409 conflict', () => {
      const err = Object.assign(new Error('membership requires conformance evidence'), { code: '23514' });
      const mapped = mapPgError(err);
      assert.ok(mapped instanceof ApiError);
      assert.equal(mapped.status, 409);
      assert.match(mapped.message, /conformance evidence/);
    });
    it('maps P0001 rule exception to 409 conflict', () => {
      const err = Object.assign(new Error('append-only; UPDATE rejected'), { code: 'P0001' });
      const mapped = mapPgError(err);
      assert.ok(mapped instanceof ApiError);
      assert.equal(mapped.status, 409);
    });
    it('returns null for non-PG errors (fall through to next)', () => {
      assert.equal(mapPgError(new Error('boom')), null);
      assert.equal(mapPgError(null), null);
      assert.equal(mapPgError(Object.assign(new Error('x'), { code: '23505' })), null);
    });
  });
});
