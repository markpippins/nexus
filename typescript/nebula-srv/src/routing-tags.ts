/**
 * Routing-tag validation for agent-record creation.
 *
 * Background (DBA inspection 26a6acb3, 2026-09-30): the inbox read path
 * matches the routing tag EXACTLY as `to:<role.toLowerCase()>`
 * (bin/check-inbox.sh; mirrors normalizeRole() in nebula-mcp's
 * tools/index.ts). A record written with an uppercase routing tag such as
 * `to:DBA` therefore matches NO inbox query, for either spelling — the tag
 * is silently undeliverable. The corpus already held 694 `to:dba` against 7
 * `to:DBA`, and the Supervisor's own ruling (24162dba, answering ac9fc4af)
 * was tagged `to:DBA` and stayed undelivered for ~6.5 hours.
 *
 * Lowercasing on the READ side fixed the lookup but left the WRITE side open:
 * nothing rejected an uppercase `to:` tag at creation. This module is that
 * missing write-side guard.
 *
 * The canonical role vocabulary is lowercase ([a-z0-9_-]+), per
 * config/roles/roles.json and the roles_name_check constraint on the
 * authority key, so requiring the suffix to be lowercase is lossless.
 */

/** A routing tag's role suffix must be canonical lowercase vocabulary. */
const ROUTING_ROLE_RE = /^[a-z0-9_-]+$/;

export const ROUTING_TAG_PREFIX = 'to:';

export interface RoutingTagProblem {
  tag: string;
  reason: string;
}

/**
 * Return every routing-tag problem in `tags`. An empty array means the tags
 * are deliverable.
 *
 * Deliberately narrow: only `to:` routing tags are checked. Other prefixes
 * (type:, status:, area:, pr:) carry no delivery semantics and are left to
 * their own vocabulary rules.
 */
export function findRoutingTagProblems(tags: unknown): RoutingTagProblem[] {
  if (tags === undefined || tags === null) return [];
  if (!Array.isArray(tags)) {
    return [{ tag: String(tags), reason: 'tags must be an array of strings' }];
  }

  const problems: RoutingTagProblem[] = [];
  for (const raw of tags) {
    if (typeof raw !== 'string') {
      problems.push({ tag: String(raw), reason: 'tag must be a string' });
      continue;
    }
    if (!raw.startsWith(ROUTING_TAG_PREFIX)) continue;

    const role = raw.slice(ROUTING_TAG_PREFIX.length);
    if (!ROUTING_ROLE_RE.test(role)) {
      problems.push({
        tag: raw,
        reason:
          `routing role must be canonical lowercase [a-z0-9_-]+, got "${role}". ` +
          `Inbox lookup matches "to:"+role.toLowerCase() exactly, so an uppercase ` +
          `variant addresses a dead mailbox and is silently undeliverable. ` +
          `Use "${ROUTING_TAG_PREFIX}${role.toLowerCase()}" instead.`,
      });
    }
  }
  return problems;
}

/** True when every routing tag in `tags` is deliverable. */
export function routingTagsAreDeliverable(tags: unknown): boolean {
  return findRoutingTagProblems(tags).length === 0;
}

/** Single-line message suitable for an HTTP 400 body. */
export function routingTagErrorMessage(tags: unknown): string | null {
  const problems = findRoutingTagProblems(tags);
  if (problems.length === 0) return null;
  const detail = problems
    .map((p) => `"${p.tag}" (${p.reason})`)
    .join('; ');
  return `undeliverable routing tag(s): ${detail}`;
}