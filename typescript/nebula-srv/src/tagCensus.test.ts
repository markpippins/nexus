/**
 * Hermetic tests for the §8 census guard (card `ef1b5077` §8, Ruling 17 F2).
 *
 * The load-bearing test here is `clean tags are never flagged`: the census
 * asserts that what the REST write path stores is, by definition, not a
 * bypass. If §4 ever changes, that property is what proves the guard did not
 * silently start flagging correct writes — and it holds only because the
 * census imports the real normalizer instead of restating the grammar.
 *
 * These tests need no database and no server.
 */

import { describe, it, expect } from 'vitest';
import { classifyRecord, runCensus, createdAtToIso, summarizeCensus } from './tagCensus';
import { normalizeTags } from './tagNormalizer';

const rec = (tags: unknown, createdAt = '2026-10-02T00:00:00.000Z', id = 'id-1') => ({
  id,
  createdAt,
  tags,
});

describe('classifyRecord — bypass forms are flagged', () => {
  it('flags an untrimmed, uppercased Class R address (the corpus defect)', () => {
    const f = classifyRecord(rec(['  to:DBA  ']));
    expect(f).not.toBeNull();
    expect(f!.reasons).toContain('class-r-not-normalized');
    expect(f!.offendingTags).toEqual(['  to:DBA  ']);
    expect(f!.normalizedForm).toEqual(['to:dba']);
  });

  it('flags a concatenated address and shows the split it would have received', () => {
    const f = classifyRecord(rec(['to:engineer-to:engineer-ii']));
    expect(f!.reasons).toContain('class-r-not-normalized');
    expect(f!.normalizedForm).toEqual(['to:engineer', 'to:engineer-ii']);
  });

  it('flags a JSON-stringified array tag as an artifact', () => {
    const f = classifyRecord(rec(['["area:inbox","type:change"]']));
    expect(f!.reasons).toContain('artifact-tag');
  });

  it('flags a comma-joined list as an artifact', () => {
    const f = classifyRecord(rec(['affects:pr:613,pr:614,pr:615']));
    expect(f!.reasons).toContain('artifact-tag');
    expect(f!.normalizedForm).toEqual(['affects:pr:613', 'pr:614', 'pr:615']);
  });

  it('flags a quote-wrapped tag as an artifact', () => {
    const f = classifyRecord(rec(['"type:change"']));
    expect(f!.reasons).toContain('artifact-tag');
  });

  it('flags an empty-string tag', () => {
    const f = classifyRecord(rec(['']));
    expect(f!.reasons).toContain('empty-tag');
  });

  it('flags duplicates, which the write path dedupes', () => {
    const f = classifyRecord(rec(['to:dba', 'to:dba']));
    expect(f!.reasons).toContain('duplicate-tag');
    expect(f!.offendingTags).toContain('to:dba');
  });

  it('flags a mixed payload and names every reason present', () => {
    const f = classifyRecord(rec(['  to:DBA  ', '["a"]', 'blocks:PR-580']));
    expect(f!.reasons).toEqual(
      expect.arrayContaining(['class-r-not-normalized', 'artifact-tag']),
    );
    // Order is normalizeTags' own: the JSON-unwrap re-queues its inner value
    // rather than emitting it inline, so `blocks:PR-580` is stored first. The
    // census reports the normalizer's order verbatim — it does not re-sort.
    expect(f!.normalizedForm).toEqual(['to:dba', 'blocks:PR-580', 'a']);
  });
});

describe('classifyRecord — clean records are never flagged', () => {
  // The fixed-point property. Every input here is what the write path stores,
  // so the census must return null for all of them.
  const clean: unknown[][] = [
    ['to:dba'],
    ['to:engineer-ii'],
    ['type:verification', 'pr:714', 'card:ef1b5077'],
    // Class V case is PRESERVED by design (1,374 legitimate uppercase tags).
    ['blocks:PR-580', 'Ruling-17'],
    // A legitimately split record: neither tag alone reconstructs the other.
    ['to:engineer', 'to:engineer-ii'],
    // Value tags the charset contract warns about but keeps verbatim.
    ['has space', 'x'.repeat(120)],
    [],
  ];

  for (const tags of clean) {
    it(`does not flag ${JSON.stringify(tags)}`, () => {
      expect(classifyRecord(rec(tags))).toBeNull();
    });
  }

  // The strongest form of the property: whatever the normalizer emits for
  // arbitrary input, feeding that output back in must be a fixed point.
  it('never flags normalizeTags output, for any dirty input', () => {
    const dirties: unknown[][] = [
      ['  to:DBA  '],
      ['to:engineer-to:engineer-ii'],
      ['["area:inbox","type:change"]'],
      ['affects:pr:613,pr:614'],
      ['', '  ', 'to:-to:engineer', 'note-to:Engineer'],
      ['to:Engineer-to:Engineer-II-to:tester'],
    ];
    for (const dirty of dirties) {
      const written = normalizeTags(dirty).tags;
      expect(classifyRecord(rec(written))).toBeNull();
    }
  });
});

describe('createdAtToIso', () => {
  it('converts epoch milliseconds (what the list endpoint returns)', () => {
    expect(createdAtToIso(1790916206164)).toBe(new Date(1790916206164).toISOString());
  });

  it('converts a stringified epoch without mis-parsing it as a year', () => {
    expect(createdAtToIso('1790916206164')).toBe(new Date(1790916206164).toISOString());
  });

  it('passes an ISO string through, normalized', () => {
    expect(createdAtToIso('2026-10-02T00:00:00Z')).toBe('2026-10-02T00:00:00.000Z');
  });
});

describe('runCensus — epoch semantics', () => {
  const corpus = [
    rec(['to:dba'], '2026-06-30T04:46:44.460Z', 'old-clean'),
    rec(['  to:DBA  '], '2026-06-30T04:46:45.000Z', 'old-dirty'),
    rec(['to:dba'], '2026-10-02T00:00:00.000Z', 'new-clean'),
    rec(['to:engineer-to:engineer-ii'], '2026-10-02T01:00:00.000Z', 'new-dirty'),
  ];

  it('reports everything as baseline and asserts nothing when no epoch is set', () => {
    const r = runCensus(corpus);
    expect(r.baselineOnly).toBe(true);
    expect(r.violations).toEqual([]);
    expect(r.findings).toHaveLength(2);
    expect(r.baseline.map((f) => f.id)).toEqual(['old-dirty', 'new-dirty']);
  });

  it('splits baseline from violations once an epoch is set', () => {
    const r = runCensus(corpus, { epochIso: '2026-10-01T00:00:00.000Z' });
    expect(r.baselineOnly).toBe(false);
    expect(r.violations.map((f) => f.id)).toEqual(['new-dirty']);
    expect(r.baseline.map((f) => f.id)).toEqual(['old-dirty']);
  });

  it('treats the epoch instant itself as post-epoch (inclusive)', () => {
    const r = runCensus([rec(['  to:DBA  '], '2026-10-01T00:00:00.000Z')], {
      epochIso: '2026-10-01T00:00:00.000Z',
    });
    expect(r.violations).toHaveLength(1);
  });

  it('rejects an unparseable epoch rather than silently asserting nothing', () => {
    expect(() => runCensus(corpus, { epochIso: 'not-a-date' })).toThrow(/parseable instant/);
  });

  it('reports a clean corpus as zero findings', () => {
    const r = runCensus([rec(['to:dba'], '2026-10-02T00:00:00.000Z')], {
      epochIso: '2026-10-01T00:00:00.000Z',
    });
    expect(r.findings).toEqual([]);
    expect(r.violations).toEqual([]);
    expect(r.scanned).toBe(1);
  });

  it('summarizes counts and reasons', () => {
    const s = summarizeCensus(runCensus(corpus));
    expect(s).toContain('scanned=4');
    expect(s).toContain('findings=2');
    expect(s).toContain('violations=0');
    expect(s).toContain('class-r-not-normalized=2');
  });
});

describe('malformed input is tolerated, never thrown on', () => {
  it('accepts null / non-array tags without crashing', () => {
    expect(() => classifyRecord(rec(null))).not.toThrow();
    expect(() => classifyRecord(rec(undefined))).not.toThrow();
    expect(() => classifyRecord(rec(42))).not.toThrow();
  });

  it('ignores non-string members of a tags array', () => {
    expect(classifyRecord(rec(['to:dba', 7, null]))).toBeNull();
  });
});