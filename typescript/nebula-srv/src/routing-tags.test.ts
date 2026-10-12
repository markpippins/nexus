/**
 * Unit tests for routing-tag validation (write-side guard).
 *
 * Background: DBA inspection 26a6acb3. The inbox read path matches
 * `to:<role.toLowerCase()>` exactly, so a record created with `to:DBA`
 * reaches no inbox. The Supervisor's ruling 24162dba was lost this way for
 * ~6.5 hours. These tests pin the write-side rejection that prevents a
 * repeat.
 *
 * Hermetic: no database, no server. Pure function.
 */
import { describe, it, expect } from 'vitest';
import {
  findRoutingTagProblems,
  routingTagsAreDeliverable,
  routingTagErrorMessage, routingCaseErrorMessage, findRoutingCaseProblems } from './routing-tags';

describe('findRoutingTagProblems', () => {
  it('accepts canonical lowercase routing tags', () => {
    expect(findRoutingTagProblems(['to:dba', 'to:architect'])).toEqual([]);
  });

  it('REJECTS the exact tag that lost the supervisor ruling', () => {
    const problems = findRoutingTagProblems(['to:DBA']);
    expect(problems).toHaveLength(1);
    expect(problems[0].tag).toBe('to:DBA');
  });

  it('rejects uppercase in any position of the role suffix', () => {
    expect(findRoutingTagProblems(['to:Engineer-III'])).toHaveLength(1);
    expect(findRoutingTagProblems(['to:dba', 'to:SuperVisor'])).toHaveLength(1);
  });

  it('rejects an empty role suffix', () => {
    expect(findRoutingTagProblems(['to:'])).toHaveLength(1);
  });

  it('rejects whitespace or punctuation in the role suffix', () => {
    expect(findRoutingTagProblems(['to:my role'])).toHaveLength(1);
    expect(findRoutingTagProblems(['to:dba.x'])).toHaveLength(1);
  });

  it('accepts the canonical vocabulary characters', () => {
    // roles_name_check enforces ^[a-z0-9_-]+ on the authority key
    expect(findRoutingTagProblems(['to:engineer-ii', 'to:engineer_iii', 'to:dba2'])).toEqual([]);
  });

  it('does NOT police non-routing tag prefixes', () => {
    // scope is routing delivery only; other prefixes keep their own rules
    expect(findRoutingTagProblems(['type:Decision', 'status:DONE', 'area:Testing'])).toEqual([]);
  });

  it('reports every offending tag, not just the first', () => {
    expect(findRoutingTagProblems(['to:DBA', 'ok', 'to:Architect'])).toHaveLength(2);
  });

  it('treats missing tags as deliverable (no routing claimed)', () => {
    expect(findRoutingTagProblems(undefined)).toEqual([]);
    expect(findRoutingTagProblems(null)).toEqual([]);
    expect(findRoutingTagProblems([])).toEqual([]);
  });

  it('rejects a non-array tags value', () => {
    expect(findRoutingTagProblems('to:dba')).toHaveLength(1);
  });

  it('rejects non-string tag entries', () => {
    expect(findRoutingTagProblems([42])).toHaveLength(1);
  });
});

describe('routingTagsAreDeliverable', () => {
  it('is true for lowercase and false for uppercase', () => {
    expect(routingTagsAreDeliverable(['to:dba', 'type:report'])).toBe(true);
    expect(routingTagsAreDeliverable(['to:DBA'])).toBe(false);
  });
});

describe('routingTagErrorMessage', () => {
  it('returns null when there is nothing wrong', () => {
    expect(routingTagErrorMessage(['to:dba'])).toBeNull();
  });

  it('names the offending tag and the corrected form', () => {
    const msg = routingTagErrorMessage(['to:DBA']);
    expect(msg).toContain('to:DBA');
    expect(msg).toContain('to:dba');
  });
});
// ── Ruling 38J: case-only routing tags are rejected at POST ──────────────
// The R13 normalizer CAN lowercase `to:DBA` → `to:dba`, but that repair is a
// routing bug historically, not a structural fix — DBA inspection 26a6acb3
// (694 to:dba vs 7 to:DBA; a record undelivered ~6.5h). POST rejects it;
// structural repairs (concat) remain the normalizer's job.
describe('routingCaseErrorMessage (Ruling 38J)', () => {
  it('rejects a case-only routing tag', () => {
    const msg = routingCaseErrorMessage(['to:DBA']);
    expect(msg).toContain('to:DBA');
    expect(msg).toContain('to:dba');
  });

  it('accepts canonical lowercase routing tags', () => {
    expect(routingCaseErrorMessage(['to:dba'])).toBeNull();
  });

  it('ignores surrounding whitespace (a structural repair, not a case change)', () => {
    expect(routingCaseErrorMessage(['  to:dba  '])).toBeNull();
    // but whitespace + case is still a case violation
    expect(routingCaseErrorMessage(['  to:DBA  '])).toContain('to:DBA');
  });

  it('leaves non-routing tags alone — case there carries no delivery semantics', () => {
    expect(routingCaseErrorMessage(['type:Report', 'blocks:PR-580'])).toBeNull();
  });

  it('ignores non-array and non-string entries', () => {
    expect(routingCaseErrorMessage(undefined)).toBeNull();
    expect(routingCaseErrorMessage('to:DBA')).toBeNull();
    expect(routingCaseErrorMessage([1, null, { a: 1 }])).toBeNull();
  });

  it('findRoutingCaseProblems returns one problem per offending tag', () => {
    const problems = findRoutingCaseProblems(['to:DBA', 'to:dba', 'to:Engineer-II']);
    expect(problems.map((p) => p.tag)).toEqual(['to:DBA', 'to:Engineer-II']);
  });
});
