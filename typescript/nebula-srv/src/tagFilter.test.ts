import { describe, it, expect } from 'vitest';
import { singleTagClause, allTagsClause, anyTagClause } from './tagFilter';

/**
 * Hermetic tests for the Ruling 8 read-side tag-clause builders.
 *
 * These verify the generated SQL shape and bind-parameter discipline only;
 * behavior against real PostgreSQL (correlated unnest semantics, lower()
 * collation, AND/OR counts) is covered by the service-tier integration test
 * tests/tag-filter-ci.integration.test.ts (service-test-gates workflow,
 * per Ruling 10's hermetic-vs-service split).
 */
describe('tagFilter clause builders (Ruling 8 read-side)', () => {
  describe('singleTagClause', () => {
    it('lowers both the SQL side and the bound parameter', () => {
      const c = singleTagClause(7, 'to:DBA');
      expect(c.sql).toContain('lower($7)');
      expect(c.sql).toContain('unnest(tags)');
      expect(c.params).toEqual(['to:dba']);
    });

    it('preserves the requested parameter index and consumes exactly one bind', () => {
      const c = singleTagClause(42, 'to:Engineer');
      expect(c.sql).toContain('lower($42)');
      expect(c.params).toHaveLength(1);
    });

    it('emits an EXISTS predicate comparable to the lowered parameter', () => {
      const c = singleTagClause(1, 'to:dba');
      expect(c.sql).toMatch(/^EXISTS \(SELECT 1 FROM unnest\(tags\) AS t WHERE lower\(t\) = lower\(\$1\)\)$/);
    });

    it('uses EXISTS — not a scalar subquery — so multi-tag rows cannot raise', () => {
      // Scalar form: (SELECT lower(t) FROM unnest(tags) AS t) = lower($1)
      // errors with "more than one row returned by a subquery used as an
      // expression" on any record with 2+ tags. Caught live by the service-tier
      // test; guarded here so the shape can never regress to scalar.
      const c = singleTagClause(1, 'to:dba');
      expect(c.sql.startsWith('EXISTS (')).toBe(true);
      expect(c.sql).not.toMatch(/^\(SELECT lower\(t\)/);
    });
  });

  describe('allTagsClause (AND)', () => {
    it('requires every tag via a lowered count comparison', () => {
      const c = allTagsClause(3, ['to:dba', 'type:Decision']);
      expect(c.sql).toContain('= ANY(ARRAY[lower($3), lower($4)])');
      expect(c.sql).toContain(') = 2');
      expect(c.params).toEqual(['to:dba', 'type:decision']);
    });

    it('numbers consecutive placeholders from startIndex', () => {
      const c = allTagsClause(5, ['a', 'b', 'c']);
      for (const n of [5, 6, 7]) expect(c.sql).toContain(`$${n}`);
      expect(c.params).toHaveLength(3);
    });

    it('with one tag degrades to count = 1 semantics, not ANY-loose OR', () => {
      const c = allTagsClause(1, ['to:dba']);
      expect(c.sql).toContain(') = 1');
      expect(c.sql).not.toContain('> 0');
    });
  });

  describe('anyTagClause (OR)', () => {
    it('matches at least one tag via a lowered count comparison', () => {
      const c = anyTagClause(2, ['to:DBA', 'to:dba']);
      expect(c.sql).toContain('= ANY(ARRAY[lower($2), lower($3)])');
      expect(c.sql).toContain('> 0');
      expect(c.params).toEqual(['to:dba', 'to:dba']);
    });

    it('numbers consecutive placeholders from startIndex', () => {
      const c = anyTagClause(9, ['x', 'y']);
      expect(c.sql).toContain('lower($9)');
      expect(c.sql).toContain('lower($10)');
      expect(c.params).toHaveLength(2);
    });
  });

  describe('invariants across builders', () => {
    it('every emitted parameter is already lowercase — callers must not lower again', () => {
      for (const c of [
        singleTagClause(1, 'MiXeD:CaSe'),
        allTagsClause(1, ['MiXeD:One', 'MiXeD:Two']),
        anyTagClause(1, ['MiXeD:One', 'MiXeD:Two']),
      ]) {
        for (const p of c.params) expect(p).toBe(String(p).toLowerCase());
      }
    });

    it('no builder interpolates caller strings into SQL (injection discipline)', () => {
      const hostile = "to:x'); DROP TABLE nebula.agent_records;--";
      for (const c of [singleTagClause(1, hostile), allTagsClause(1, [hostile]), anyTagClause(1, [hostile])]) {
        expect(c.sql).not.toContain(hostile);
        expect(c.sql).not.toContain('DROP');
        expect(c.params).toContain(hostile.toLowerCase());
      }
    });
  });
});
