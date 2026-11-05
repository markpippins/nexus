/**
 * Write-side guard for agent records that carry no information at all.
 *
 * Why this exists (2026-11-04): `POST /api/agent-records` validated
 * `recordType`, `level` and routing tags, but never title or content. Two
 * `role=tester` rows (`65d01e3f`, `b1e328c4`, 00:20:52Z/00:20:54Z) were
 * therefore accepted with an empty title, an empty tag list and a zero-length
 * body. They are unreadable, unroutable and unattributable: nothing can search
 * them, no inbox can receive them, and no gate can evaluate them. A row that
 * cannot be read is not a record, and accepting it moves a silent failure from
 * the writer to every later reader.
 *
 * The rule is deliberately the narrowest one that rejects those rows: a record
 * is refused only when it has NEITHER a title NOR a body. It is NOT a demand
 * for prose — 47 records in the corpus legitimately carry an empty body with a
 * meaningful title (status projections, metadata-only rows), and 5 carry a
 * title-only shape. Requiring a body would have rejected all of those to catch
 * an accident that has so far produced three rows.
 *
 * Out of scope on purpose: this is not attestation policy. A bodyless
 * *attestation* is worthless evidence, but refusing it is the gate's job
 * (gate 3 re-verifies every cited run), not this guard's — and a rule here
 * would silently become a rule about every record type.
 */

/** Recorded so the 400 body and the tests cannot drift apart. */
export const BLANK_RECORD_ERROR =
  'agent record requires a title or content: both are empty, so the record ' +
  'would be unreadable and unroutable (refusing the write rather than storing ' +
  'a blank row — see records 65d01e3f/b1e328c4)';

/**
 * True when a value carries no text: absent, or only whitespace.
 *
 * Non-string, non-nullish values are treated as PRESENT: coercion and type
 * validation belong to the route/DB, and this guard must not start rejecting
 * shapes it does not administer.
 */
export function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value !== 'string') return false;
  return value.trim().length === 0;
}

/**
 * Returns a 400-ready message when the record is blank on both fields, or
 * `null` when the write may proceed.
 */
export function blankRecordError(title: unknown, content: unknown): string | null {
  return isBlank(title) && isBlank(content) ? BLANK_RECORD_ERROR : null;
}

/**
 * Optional attribution: which harness run wrote this record.
 *
 * Why it exists (2026-11-05): `bin/check_harness_silent_success.py` had to
 * match records to runs by role and timestamp, because a record carried no run
 * id. That heuristic clears a silent run as soon as ANY record from the same
 * role lands in the window. harness-srv already exports the job uuid to its
 * children as `HARNESS_JOB_ID`, so the record can simply say which run it
 * belongs to and the checker can compare ids instead of guessing.
 *
 * The value is validated rather than trusted, and the format is deliberately
 * loose: this fleet's harnesses emit uuids and prompt-file basenames, and a
 * job id is opaque to nebula-srv. What is refused is anything that cannot be a
 * run identifier at all (empty after trim, spaces, path separators, 128+
 * chars) — storing it would look like attribution while matching nothing,
 * which is worse than leaving the row unattributed.
 */
const HARNESS_JOB_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;

export const HARNESS_JOB_ID_INVALID =
  'harnessJobId must be 8-128 characters of [A-Za-z0-9._:-] starting with an ' +
  'alphanumeric character; omit it to write an unattributed record';

/**
 * Normalize the optional `harnessJobId` write field.
 *
 * `{ jobId: null, error: null }` when the caller omitted it (or sent an empty
 * string — the shape a shell hands over for an unset variable), `{ jobId }`
 * when it validated, `{ jobId: null, error }` when it did not.
 */
export function normalizeHarnessJobId(
  value: unknown
): { jobId: string | null; error: string | null } {
  if (value === null || value === undefined) return { jobId: null, error: null };
  if (typeof value !== 'string') {
    return { jobId: null, error: HARNESS_JOB_ID_INVALID };
  }
  const trimmed = value.trim();
  if (trimmed.length === 0) return { jobId: null, error: null };
  if (!HARNESS_JOB_ID_RE.test(trimmed)) {
    return { jobId: null, error: HARNESS_JOB_ID_INVALID };
  }
  return { jobId: trimmed, error: null };
}
