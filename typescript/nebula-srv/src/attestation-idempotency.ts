// ── Write-time (pr, head) idempotency for tester attestations ───────────────
// The tester harness files a NEW attestation row on every retry. When several
// retries land on one head, only the newest row is the gate's answer, and the
// older rows stayed live `status:done` records for good (#1044 carried three
// live rows for a single head until they were retired by hand).
//
// The merge gate now retires same-head duplicates when it evaluates a PR
// (bin/merge_pr.py `duplicate_attestations`), which repairs the history *after*
// the fact — but only for a PR something gates again. This module closes the
// same gap at WRITE time, so the predecessor is retired the moment its
// successor lands and a duplicate never accumulates in the corpus.
//
// Scope is deliberately narrow, mirroring the gate's duplicate rule:
//   - role `tester`, attestation shape (canonical: recordType=assessment with
//     `type:approval` + `status:done`; legacy: `type:attestation`);
//   - the NEW row must carry BOTH a `pr:<N>`/`gate:<N>` tag AND a well-formed
//     `head:<sha>` tag. Without the head binding there is nothing to be
//     idempotent ON, and head-less legacy rows must never be collapsed — a
//     handful of old pairs are shared by two PRs (679/681) precisely because
//     they carry no head to tell them apart;
//   - only rows whose `head:` tag prefix-matches (7–40 hex, either direction,
//     the gate's rule) and that are still live are retired;
//   - retirement is Decision 24 Amendment 1 — the same status transition
//     bin/supersede-record.sh retire applies (drop every `status:*`, add
//     `status:retired`, prefix the title, record `metadata['record.retired']`, body
//     UNCHANGED). The server must not invent a second retirement dialect, and
//     the content survives because only the status moves.
//
// Import-safe (no DB/service imports), like agent-record-projection.ts, so the
// contract is unit-testable without the routes.ts import graph.

/** Status tags that WITHDRAW a record while leaving its shape tags intact. */
export const WITHDRAWN_STATUS_TAGS: ReadonlySet<string> = new Set([
  'status:retired',
  'status:superseded',
]);

// The gate's grammar for a minted `head:` binding (Decision 15 item C):
// 7-40 lowercase hex. A malformed tag is NOT a binding — it is ignored here,
// exactly as a missing one is, because collapsing rows on a 4-hex "head" would
// bind at roughly 1/65536.
const HEAD_TAG_RE = /^[0-9a-f]{7,40}$/;
const PR_TAG_RE = /^(?:pr|gate):(\d+)$/i;

export interface AttestationBinding {
  /** PR number from the `pr:<N>` or `gate:<N>` tag. */
  pr: number;
  /** Lowercased `head:<sha>` value (7-40 hex). */
  head: string;
}

/** The subset of an agent-record row these helpers read. */
export interface AgentRecordRow {
  id?: string;
  role?: string | null;
  recordType?: string | null;
  record_type?: string | null;
  title?: string | null;
  tags?: string[] | null;
  metadata?: Record<string, any> | null;
}

function tagList(tags: string[] | null | undefined): string[] {
  return (tags ?? []).map((t) => String(t ?? ''));
}

/** The row's `head:<sha>` value (lowercased), or null when absent/malformed. */
export function headTag(tags: string[] | null | undefined): string | null {
  for (const tag of tagList(tags)) {
    if (tag.toLowerCase().startsWith('head:')) {
      const value = tag.slice('head:'.length).trim().toLowerCase();
      return value || null;
    }
  }
  return null;
}

/** The PR number named by a `pr:<N>` or `gate:<N>` tag, or null. */
export function prTag(tags: string[] | null | undefined): number | null {
  for (const tag of tagList(tags)) {
    const m = PR_TAG_RE.exec(tag.trim());
    if (m) return parseInt(m[1], 10);
  }
  return null;
}

/** True when the row was retired or superseded (Decision 24). */
export function isWithdrawn(tags: string[] | null | undefined): boolean {
  return tagList(tags).some((t) => WITHDRAWN_STATUS_TAGS.has(t.toLowerCase()));
}

/**
 * Fail-closed attestation-shape test — the same rule as
 * merge_pr.is_attestation_record(): canonical (`assessment` + `type:approval`
 * + `status:done`) or legacy (`type:attestation`). An intent row is not an
 * attestation, so it never retires a real one.
 */
export function isAttestationShaped(
  recordType: string | null | undefined,
  tags: string[] | null | undefined,
): boolean {
  const lower = new Set(tagList(tags).map((t) => t.toLowerCase()));
  if (lower.has('type:attestation')) return true;
  return (
    String(recordType ?? '').toLowerCase() === 'assessment' &&
    lower.has('type:approval') &&
    lower.has('status:done')
  );
}

/**
 * The (pr, head) a NEW record is an attestation FOR, or null when the record
 * cannot be idempotent-keyed: not a tester attestation, no PR tag, or no
 * well-formed head tag. Null means "store it and retire nothing" — the
 * head-less legacy rows must keep their meaning.
 */
export function attestationBinding(
  role: string | null | undefined,
  recordType: string | null | undefined,
  tags: string[] | null | undefined,
): AttestationBinding | null {
  if (String(role ?? '').toLowerCase() !== 'tester') return null;
  if (!isAttestationShaped(recordType, tags)) return null;
  const pr = prTag(tags);
  const head = headTag(tags);
  if (pr === null || !head || !HEAD_TAG_RE.test(head)) return null;
  return { pr, head };
}

/**
 * Prefix comparison either direction, 7-40 hex on both sides — the gate's
 * binding rule (a minted tag may carry 7 to 40 hex chars). A malformed or
 * absent value never matches: no binding to disprove is NOT a match here,
 * because this path MUTATES the candidate row.
 */
export function headsMatch(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  const x = String(a ?? '').trim().toLowerCase();
  const y = String(b ?? '').trim().toLowerCase();
  if (!HEAD_TAG_RE.test(x) || !HEAD_TAG_RE.test(y)) return false;
  return x.startsWith(y) || y.startsWith(x);
}

/**
 * Live rows the new record supersedes: same PR, same head binding, still live,
 * attestation-shaped, tester, and not the successor itself.
 *
 * Refused rows are INCLUDED (unlike merge_pr.duplicate_attestations, which
 * leaves them to its refusal path): there is no refusal path on the write
 * side, and a `status:refused` row is equally not the current answer for this
 * (pr, head). Withdrawn rows are excluded — retirement is not re-applied.
 */
export function supersedableDuplicates(
  rows: AgentRecordRow[] | null | undefined,
  binding: AttestationBinding,
  successorId: string,
): AgentRecordRow[] {
  const successor = String(successorId ?? '');
  const out: AgentRecordRow[] = [];
  for (const row of rows ?? []) {
    const id = String(row?.id ?? '');
    if (!id || id === successor) continue;
    if (String(row?.role ?? '').toLowerCase() !== 'tester') continue;
    const recordType = row?.recordType ?? row?.record_type ?? null;
    if (!isAttestationShaped(recordType, row?.tags)) continue;
    if (isWithdrawn(row?.tags)) continue;
    if (prTag(row?.tags) !== binding.pr) continue;
    if (!headsMatch(headTag(row?.tags), binding.head)) continue;
    out.push(row);
  }
  return out;
}

/**
 * The Decision 24 Amendment 1 retirement patch for a superseded attestation:
 * every `status:*` drops (a row that is `status:done` AND `status:retired`
 * still reads as live to a tag query), `status:retired` is added, the title is
    * prefixed once, and `metadata['record.retired']` names the successor. The body is not
 * touched — retirement is a status transition, not a rewrite.
 */
export function retirementPatch(
  row: AgentRecordRow,
  binding: AttestationBinding,
  successorId: string | null | undefined,
  atIso: string,
): { title: string; tags: string[]; metadata: Record<string, any> } | null {
  if (isWithdrawn(row.tags)) return null;
  const kept = tagList(row.tags).filter(
    (t) => !t.toLowerCase().startsWith('status:'),
  );
  const prefix = `[RETIRED ${atIso.slice(0, 10)}] `;
  const title = String(row.title ?? '');
  const metadata: Record<string, any> = { ...(row.metadata ?? {}) };
  const successor = String(successorId ?? '').slice(0, 8);
  // Canonical owner-prefixed key (metadata namespace ruling, item 6): the
  // retirement lifecycle is the record component's contract, not free-form
  // prose. Legacy rows carry `retired` until the namespace normalization moves
  // them; readers accept both spellings.
  metadata['record.retired'] = {
    at: atIso,
    note:
      `duplicate attestation for PR #${binding.pr} at head ` +
      `${binding.head.slice(0, 8)} superseded at write time` +
      (successor ? ` by ${successor}` : ''),
    by: 'tester',
    performed_by: 'nebula-srv',
    convention: 'Decision 24 (cfcc9d65) Amendment 1',
  };
  return {
    title: title.startsWith(prefix) ? title : prefix + title,
    tags: [...kept, 'status:retired'],
    metadata,
  };
}
