/**
 * Agent-record metadata namespace (ranking item 6).
 *
 * `metadata` is free-form jsonb, and the corpus measured on 2026-11-23 showed
 * what that costs: 524 distinct top-level keys, with system components writing
 * their state under bare English words any record author may also use
 * (`retired`, `corrections`, `snapshot_purpose`, `_migrated_from`, …).
 *
 * The architect ruling for item 6 makes those contracts explicit: a system
 * component owns a dotted prefix — `record.`, `correction.`, `snapshot.`,
 * `migration.`, `repair.`, `harness.` — and the component's key is
 * `<owner>.<key>`. Record-authored keys stay free-form: they belong to the
 * record, not to a component, and renaming them would invent a contract no
 * producer signed.
 *
 * This module is the single place that knows the legacy spellings, so both
 * write paths (POST and PATCH) reject them with the canonical name in the
 * message, and the projections can price the migration the same way.
 */

/** Legacy un-namespaced tool key -> the canonical name that replaces it. */
export const LEGACY_OWNED_KEYS: Readonly<Record<string, string>> = Object.freeze({
  retired: 'record.retired',
  corrections: 'correction.claims',
  residue_closed: 'correction.residue_closed',
  snapshot_purpose: 'snapshot.purpose',
  _migrated_from: 'migration.origin_path',
  _dirname: 'migration.dirname',
  _size_bytes: 'migration.size_bytes',
  hollow_content_repair: 'repair.hollow_content',
  hollow_content_audit: 'repair.hollow_content_audit',
  repaired_from: 'repair.repaired_from',
  restored_content_chars: 'repair.restored_chars',
});

/** The closed set of owners a canonical key may name. */
export const METADATA_OWNERS: readonly string[] = Object.freeze([
  'record', 'correction', 'snapshot', 'migration', 'repair', 'harness',
]);

/**
 * The write-time guard: null when the metadata is acceptable, otherwise a
 * message naming every offending key and its canonical replacement.
 *
 * Only tool-owned legacy keys are refused. Free-form keys — the record's own
 * prose, e.g. `pr`, `branch`, `verdict` — pass through untouched; they are not
 * a contract this service can own or rename.
 */
export function metadataNamespaceError(metadata: unknown): string | null {
  if (metadata === null || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return null;
  }
  const hits: string[] = [];
  for (const key of Object.keys(metadata as Record<string, unknown>)) {
    const canonical = LEGACY_OWNED_KEYS[key];
    if (canonical) hits.push(`${key} -> ${canonical}`);
  }
  if (hits.length === 0) return null;
  return (
    'metadata uses legacy un-namespaced tool keys ' +
    `(${hits.join(', ')}); write the canonical <owner>.<key> name instead ` +
    '(metadata namespace ruling, item 6). Record-authored free-form keys are unchanged.'
  );
}

/** True when `key` is already a canonical owner-prefixed key. */
export function isCanonicalMetadataKey(key: string): boolean {
  const dot = key.indexOf('.');
  if (dot <= 0) return false;
  return METADATA_OWNERS.includes(key.slice(0, dot));
}
