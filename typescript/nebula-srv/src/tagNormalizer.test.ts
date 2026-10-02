import { describe, it, expect } from 'vitest';
import { normalizeTags, TAG_MAX_LENGTH } from './tagNormalizer';

/**
 * Hermetic tests for the write-side tag normalizer (ratified tag-grammar
 * card §4 + Ruling 13). Service-tier behavior (API wiring, storage) is
 * covered by tests/tag-normalizer.integration.test.ts per Ruling 10.
 */
describe('tagNormalizer (card §4)', () => {
  describe('§4.1 trim + drop empties', () => {
    it('trims whitespace and drops empty/whitespace-only tags', () => {
      expect(normalizeTags(['  type:change  ', '', '   ', 'area:inbox'])).toEqual({
        tags: ['type:change', 'area:inbox'],
        warnings: [],
      });
    });

    it('handles null/undefined as empty', () => {
      expect(normalizeTags(null).tags).toEqual([]);
      expect(normalizeTags(undefined).tags).toEqual([]);
      expect(normalizeTags(null).warnings).toEqual([]);
    });

    it('coerces non-string scalars', () => {
      expect(normalizeTags([42, true] as any).tags).toEqual(['42', 'true']);
    });
  });

  describe('§4.2a JSON-unwrap (corpus defect class)', () => {
    it('unwraps a JSON-stringified array passed as a single tag', () => {
      const dirty = JSON.stringify(['type:change', 'area:inbox']);
      expect(normalizeTags([dirty]).tags).toEqual(['type:change', 'area:inbox']);
    });

    it('unwraps nested JSON-stringified arrays (bounded depth)', () => {
      const nested = JSON.stringify([JSON.stringify(['a:b'])]);
      expect(normalizeTags([nested]).tags).toEqual(['a:b']);
    });

    it('unwraps a JSON object into key/value tags', () => {
      expect(normalizeTags([JSON.stringify({ area: 'inbox', type: 'change' })]).tags).toEqual([
        'inbox',
        'area',
        'change',
        'type',
      ]);
    });

    it('recovers trailing-bracket residue like \\"fix\\"] via strip (not JSON)', () => {
      // Not parseable JSON — must be cleaned by the strip rule, not dropped.
      expect(normalizeTags(['"fix"]']).tags).toEqual(['fix']);
      expect(normalizeTags(['["infra"']).tags).toEqual(['infra']);
    });

    it('never throws on garbage input', () => {
      const garbage = ['{', '[', '}', ']', '"', '\\\\', ',,,', '{"a":'];
      expect(() => normalizeTags(garbage)).not.toThrow();
      expect(normalizeTags(garbage).tags.every((t) => t.length > 0)).toBe(true);
    });
  });

  describe('§4.2b comma-joined lists', () => {
    it('splits a comma-joined list into multiple tags (card fixture)', () => {
      expect(normalizeTags(['affects:pr:613,pr:614,pr:615']).tags).toEqual([
        'affects:pr:613',
        'pr:614',
        'pr:615',
      ]);
    });

    it('splits comma-joined bare tokens', () => {
      expect(normalizeTags(['fix,campaign,merge-conflict']).tags).toEqual([
        'fix',
        'campaign',
        'merge-conflict',
      ]);
    });
  });

  describe('§4.2c artifact stripping', () => {
    it('strips stray brackets/quotes/braces and warns', () => {
      const r = normalizeTags(['"type:change"']);
      expect(r.tags).toEqual(['type:change']);
      expect(r.warnings.some((w) => w.reason === 'charset')).toBe(true);
    });
  });

  describe('§4.3 classification: Class R lowercased, Class V preserved', () => {
    it('lowercases to: addresses (Class R)', () => {
      expect(normalizeTags(['to:DBA', 'TO:Engineer']).tags).toEqual(['to:dba', 'to:engineer']);
    });

    it('preserves value-tag case (Class V) — the 1,374-occurrence contract', () => {
      expect(normalizeTags(['2026-W40', 'ADR-006', 'blocks:PR-580', 'closes-finding:A']).tags).toEqual([
        '2026-W40',
        'ADR-006',
        'blocks:PR-580',
        'closes-finding:A',
      ]);
      expect(normalizeTags(['2026-W40']).warnings).toEqual([]);
    });

    it('lowercases the address part of a comma-joined to: list', () => {
      expect(normalizeTags(['TO:dba,TO:architect']).tags).toEqual(['to:dba', 'to:architect']);
    });

    it('dedupes case-insensitively for Class R, exactly for Class V', () => {
      expect(normalizeTags(['to:DBA', 'to:dba']).tags).toEqual(['to:dba']);
      expect(normalizeTags(['ADR-006', 'ADR-006']).tags).toEqual(['ADR-006']);
    });
  });

  describe('§4.4 WARN-not-reject on unrepaired tags', () => {
    it('keeps an over-length tag and warns (never drops, never rejects)', () => {
      const long = 'x'.repeat(TAG_MAX_LENGTH + 1);
      const r = normalizeTags([long]);
      expect(r.tags).toEqual([long]);
      expect(r.warnings).toEqual([{ tag: long, reason: 'over-length' }]);
    });

    it('keeps a charset-odd value tag and warns (parenthesis — §6: warn, accept, never rewrite meaning)', () => {
      // Parens are NOT in the strip set (that set is JSON-artifact residue
      // only); a parens tag is a §6 WARN-and-ACCEPT, kept verbatim.
      const r = normalizeTags(['weird(tag)']);
      expect(r.tags).toEqual(['weird(tag)']);
      expect(r.warnings).toEqual([{ tag: 'weird(tag)', reason: 'charset' }]);
    });

    it('charset-odd tag that survives cleaning warns and passes', () => {
      const r = normalizeTags(['has#hash']);
      expect(r.tags).toEqual(['has#hash']);
      expect(r.warnings).toEqual([{ tag: 'has#hash', reason: 'charset' }]);
    });

    it('NO rejection of unknown to: addresses (that is #693, post-#712)', () => {
      const r = normalizeTags(['to:wr-conf-observer', 'to:some-future-role']);
      expect(r.tags).toEqual(['to:wr-conf-observer', 'to:some-future-role']);
      expect(r.warnings).toEqual([]);
    });
  });

  describe('idempotence + cleanliness invariants', () => {
    it('normalizer output is a fixed point (idempotent)', () => {
      const dirty = ['  to:DBA ', '"fix"]', 'affects:pr:613,pr:614', JSON.stringify(['x:y'])];
      const once = normalizeTags(dirty).tags;
      const twice = normalizeTags(once).tags;
      expect(twice).toEqual(once);
    });

    it('output never contains empties, commas, or bracket/quote chars', () => {
      const r = normalizeTags(['  ', '"a"]', 'b,c', '[d', JSON.stringify(['e'])]);
      for (const t of r.tags) {
        expect(t.trim().length).toBeGreaterThan(0);
        expect(t).not.toMatch(/[,\\[\\]{}"']/);
      }
    });
  });
});
