/**
 * Case-insensitive tag filter clause builders for agent-record queries.
 *
 * Ruling 8 (dda5d9f7, 2026-10-01): vocabulary normalization must PRECEDE
 * reject-enforcement. Its read-side half: a record tagged `to:DBA` must be
 * deliverable to a query for `to:dba`, because write-side case variants
 * survive in the corpus (immutable history cannot be retagged — e.g. the
 * supervisor re-file a471653b is tagged `to:DBA`) and an exact-match filter
 * silently addresses a different, usually empty, mailbox.
 *
 * Previously these clauses were exact-match operators (`$1 = ANY(tags)`,
 * `tags @> $n::text[]`, `tags && $n::text[]`). Case-insensitivity is
 * achieved by lowering BOTH sides: the query parameter, and each stored tag
 * via a correlated `unnest(tags)` — same per-row conjunction semantics, with
 * no change to the AND/OR shapes the callers already rely on.
 *
 * The `@>`/`&&` array-operator forms were intentionally NOT preserved: no
 * GIN index exists on `nebula.agent_records.tags` (verified 2026-10-01), so
 * those operators were already sequential scans and the correlated-unnest
 * form costs nothing extra at corpus scale (~14.6k rows). Revisit only if a
 * GIN index is ever added.
 *
 * All values are passed as bind parameters — the caller's parameter
 * numbering (`i`) is advanced by the returned placeholder count.
 */

export const TAG_CLAUSE_DOC =
  'lower(unnest) on both sides — see Ruling 8 read-side (module header)';

/** Number of bind parameters a clause returned by these builders consumes. */
export type TagClause = { sql: string; params: unknown[] };

function lowered(value: string): string {
  return value.toLowerCase();
}

/**
 * Single-tag, case-insensitive membership: `EXISTS (SELECT 1 FROM unnest(tags)
 * WHERE lower(t) = lower($n))`. Replaces the exact `$1 = ANY(tags)` form.
 *
 * MUST be EXISTS, not a scalar `(SELECT lower(t) FROM unnest(tags) AS t) =
 * lower($n)`: the scalar form raises "more than one row returned by a
 * subquery used as an expression" on any record carrying two or more tags
 * (caught by the service-tier test's first run against multi-tag rows).
 */
export function singleTagClause(paramIndex: number, tag: string): TagClause {
  return {
    sql: `EXISTS (SELECT 1 FROM unnest(tags) AS t WHERE lower(t) = lower($${paramIndex}))`,
    params: [lowered(tag)],
  };
}

/**
 * Multi-tag AND, case-insensitive: every requested tag must be present.
 * Replaces the exact `tags @> $n::text[]` form.
 */
export function allTagsClause(startIndex: number, tags: string[]): TagClause {
  const loweredTags = tags.map(lowered);
  const placeholders = loweredTags.map((_, k) => `lower($${startIndex + k})`).join(', ');
  return {
    sql: `(SELECT count(*) FROM unnest(tags) AS t WHERE lower(t) = ANY(ARRAY[${placeholders}])) = ${loweredTags.length}`,
    params: loweredTags,
  };
}

/**
 * Multi-tag OR, case-insensitive: at least one requested tag must be present.
 * Replaces the exact `tags && $n::text[]` form.
 */
export function anyTagClause(startIndex: number, tags: string[]): TagClause {
  const loweredTags = tags.map(lowered);
  const placeholders = loweredTags.map((_, k) => `lower($${startIndex + k})`).join(', ');
  return {
    sql: `(SELECT count(*) FROM unnest(tags) AS t WHERE lower(t) = ANY(ARRAY[${placeholders}])) > 0`,
    params: loweredTags,
  };
}
