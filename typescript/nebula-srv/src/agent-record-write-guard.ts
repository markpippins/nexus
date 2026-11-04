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
