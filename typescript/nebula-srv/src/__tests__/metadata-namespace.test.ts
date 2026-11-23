/**
 * Metadata namespace guard (ranking item 6).
 *
 * The corpus carried 524 un-namespaced top-level metadata keys, with system
 * components writing their contracts under bare English words. The ruling makes
 * the contract explicit — a component owns `<owner>.<key>` — and this guard is
 * what stops the dialect from growing back: both write paths refuse a legacy
 * tool-owned key and name its canonical replacement, while free-form keys a
 * record authored itself pass untouched.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'fs';
import * as path from 'path';
import {
  LEGACY_OWNED_KEYS,
  METADATA_OWNERS,
  isCanonicalMetadataKey,
  metadataNamespaceError,
} from '../metadata-namespace';

describe('metadataNamespaceError', () => {
  it('accepts absent / empty / non-object metadata', () => {
    expect(metadataNamespaceError(undefined)).toBeNull();
    expect(metadataNamespaceError(null)).toBeNull();
    expect(metadataNamespaceError({})).toBeNull();
    expect(metadataNamespaceError([])).toBeNull();
    expect(metadataNamespaceError('retired')).toBeNull();
  });

  it('accepts canonical owner-prefixed keys', () => {
    expect(
      metadataNamespaceError({
        'record.retired': { at: '2026-11-23T06:00:00Z' },
        'correction.claims': [{ claim: 'x' }],
        'snapshot.purpose': 'pre-fk-migration-evidence',
        'repair.hollow_content': true,
      })
    ).toBeNull();
  });

  it('accepts record-authored free-form keys without renaming them', () => {
    expect(
      metadataNamespaceError({
        pr: 1049, branch: 'item6', commit: 'abc1234', model: 'gpt', role: 'tester',
        verdict: 'approved', date: '2026-11-23', session: 's1', head: 'deadbeef',
      })
    ).toBeNull();
  });

  it('refuses every legacy tool-owned key and names the canonical spelling', () => {
    for (const [legacy, canonical] of Object.entries(LEGACY_OWNED_KEYS)) {
      const message = metadataNamespaceError({ [legacy]: 1 });
      expect(message).not.toBeNull();
      expect(message).toContain(`${legacy} -> ${canonical}`);
    }
  });

  it('refuses a canonical key mixed with a legacy one, listing only the legacy', () => {
    const message = metadataNamespaceError({
      'record.retired': { at: 'x' },
      retired: { at: 'x' },
      pr: 7,
    })!;
    expect(message).toContain('retired -> record.retired');
    expect(message).not.toContain('record.retired -> ');
    expect(message).not.toContain('pr ->');
  });

  it('names every offending key in one message', () => {
    const message = metadataNamespaceError({
      retired: 1, corrections: 2, _migrated_from: 'x', hollow_content_repair: true,
    })!;
    for (const canonical of [
      'record.retired', 'correction.claims', 'migration.origin_path', 'repair.hollow_content',
    ]) {
      expect(message).toContain(canonical);
    }
  });
});

describe('canonical key shape', () => {
  it('knows the closed owner set', () => {
    expect([...METADATA_OWNERS]).toEqual([
      'record', 'correction', 'snapshot', 'migration', 'repair', 'harness',
    ]);
  });

  it('recognises owner-prefixed keys and rejects bare or foreign ones', () => {
    expect(isCanonicalMetadataKey('record.retired')).toBe(true);
    expect(isCanonicalMetadataKey('correction.claims')).toBe(true);
    expect(isCanonicalMetadataKey('harness.job_id')).toBe(true);
    expect(isCanonicalMetadataKey('retired')).toBe(false);
    expect(isCanonicalMetadataKey('pr')).toBe(false);
    expect(isCanonicalMetadataKey('other.thing')).toBe(false);
    expect(isCanonicalMetadataKey('.leading')).toBe(false);
  });
});

describe('write-path wiring', () => {
  const routes = readFileSync(path.join(__dirname, '..', 'routes.ts'), 'utf8');

  it('guards the create path', () => {
    expect(routes).toMatch(/const createMetadataProblem = metadataNamespaceError\(metadata\);/);
    expect(routes).toMatch(
      /createMetadataProblem\) \{\s*return res\.status\(422\)\.json\(\{ error: createMetadataProblem \}\);/
    );
  });

  it('guards the patch path too, so PATCH is not the bypass', () => {
    expect(routes).toMatch(/const patchMetadataProblem = metadataNamespaceError\(metadata\);/);
    expect(routes).toMatch(
      /patchMetadataProblem\) \{\s*return res\.status\(422\)\.json\(\{ error: patchMetadataProblem \}\);/
    );
  });
});
