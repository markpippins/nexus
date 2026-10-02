/**
 * Write-side tag normalizer — ratified tag-grammar card §4 (doctrine thread
 * 22a0c49e, ratification ef1b5077) + Ruling 13 (address KIND taxonomy).
 *
 * Scope contract (Ruling 8 order): this module REPAIRS and NORMALIZES only.
 * It never rejects and never drops a recoverable tag. Reject-enforcement of
 * unknown Class R addresses is #693's scope, sequenced AFTER roles.json
 * normalization (#712) merges. Unknown `to:` addresses WARN and pass through.
 *
 * Class R (routing): tags starting `to:` — address lowercased. Class V
 * (value): everything else — case PRESERVED (1,374 legitimate uppercase
 * occurrences in the corpus census; audit 331ac2f3).
 *
 * Repairs applied, in card §4 order:
 *   1. trim; drop empties/whitespace-only
 *   2. unwrap JSON-stringified arrays (defect class: callers passing
 *      JSON.stringify(tags) as a single tag — corpus evidence `"fix"]`,
 *      `["infra"`, `"type:change"`); split comma-joined lists into multiple
 *      tags (defect: `affects:pr:613,pr:614,pr:615`); strip stray bracket /
 *      quote / brace characters (zero legitimate occurrences per census)
 *   2d. split-repair concatenated Class R addresses (Ruling 13 decision 3:
 *      defect `to:engineer-to:engineer-ii` = `to:engineer` + `to:engineer-ii`;
 *      role keys contain no colons, so every later `to:` starts a new
 *      address; joiner hyphens trimmed, bare `to:` segments unrecoverable)
 *   3. lowercase Class R addresses
 *   4. WARN (never reject) on what remains unrepaired: >80 chars, or
 *      characters outside the Class V charset contract
 *
 * R13 alias expansion (to:all -> every role, etc.) is deliberately NOT
 * implemented here: aliases have no registered expansion targets yet; when
 * the alias registry lands, expansion hooks into step 3's Class R branch.
 */

/** Maximum length of a single tag (card §1 Class V contract). */
export const TAG_MAX_LENGTH = 80;

/** Characters stripped from any tag (zero legitimate occurrences in census). */
const STRIP_CHARS = /[\[\]{}"']/g;

/** Class V charset contract: what a well-formed tag may contain. */
const CLASS_V_CHARSET = /^[A-Za-z0-9:_./-]+$/;

/** A warning surfaced to the caller for loud logging at the write site. */
export interface TagWarning {
  tag: string;
  reason:
    | 'over-length'
    | 'concatenated-address'
    | 'charset'
    | 'unknown-role-address'
    | 'nested-json'
    | 'address-with-comma';
}

export interface NormalizeResult {
  tags: string[];
  warnings: TagWarning[];
}

/** Bound the JSON-unwrap loop: a sane tags array never nests this deep. */
const MAX_JSON_UNWRAP_DEPTH = 5;

function parseMaybeJsonArray(raw: string): { value: unknown; depth: number } | null {
  const s = raw.trim();
  if (s.length < 2) return null;
  const first = s[0];
  const last = s[s.length - 1];
  const isWrapped =
    (first === '[' && last === ']') ||
    (first === '{' && last === '}') ||
    (first === '"' && last === '"' && s.length > 3 && s.includes(',') === false);
  if (!isWrapped) return null;
  try {
    return { value: JSON.parse(s), depth: 1 };
  } catch {
    return null;
  }
}

/**
 * §4.2d (Ruling 13 decision 3): split-repair concatenated Class R addresses.
 * A `to:` tag containing a second `to:` after position 3 is a concatenation
 * defect (`to:engineer-to:engineer-ii` = `to:engineer` + `to:engineer-ii`).
 * Role keys contain no colons, so every subsequent `to:` starts a new
 * address; a hyphen left at the join is trimmed. A bare `to:` segment (no
 * address) is unrecoverable and dropped — the caller's warning on the
 * original tag preserves the evidence. Case is preserved; the caller
 * lowercases each Class R segment.
 */
function splitConcatenatedAddresses(tag: string): { concat: boolean; segments: string[] } {
  const lower = tag.toLowerCase();
  const starts: number[] = [];
  let idx = lower.indexOf('to:');
  while (idx !== -1) {
    starts.push(idx);
    idx = lower.indexOf('to:', idx + 3);
  }
  if (starts.length < 2) return { concat: false, segments: [tag] }; // single address
  const segments: string[] = [];
  for (let i = 0; i < starts.length; i++) {
    const end = i + 1 < starts.length ? starts[i + 1] : tag.length;
    const seg = tag.slice(starts[i], end).trim().replace(/-+$/, '');
    if (seg.length > 3) segments.push(seg); // >3 excludes bare `to:`
  }
  if (segments.length === 0) {
    // Degenerate (e.g. `to:to:`) — unrecoverable; pass through, warned.
    return { concat: true, segments: [tag] };
  }
  return { concat: true, segments };
}

/**
 * Normalize an incoming tags value of arbitrary shape (string, array,
 * JSON-stringified array, comma-joined string, null/undefined) into a clean
 * tag array per the ratified grammar. Pure: no I/O, no rejection.
 */
export function normalizeTags(input: unknown): NormalizeResult {
  const warnings: TagWarning[] = [];
  const out: string[] = [];
  const seen = new Set<string>();

  const push = (tag: string): void => {
    if (!seen.has(tag)) {
      seen.add(tag);
      out.push(tag);
    }
  };

  /** Class R address path: lowercase, warn on case change / over-length. */
  const pushAddress = (raw: string): void => {
    const lowered = 'to:' + raw.slice(3).toLowerCase();
    if (lowered !== raw) warnings.push({ tag: raw, reason: 'charset' });
    if (lowered.length > TAG_MAX_LENGTH) {
      warnings.push({ tag: lowered, reason: 'over-length' });
    }
    push(lowered);
  };

  // ── seed queue: accept arrays, single strings, nested JSON strings ──
  const queue: Array<{ value: unknown; depth: number }> = [];
  if (Array.isArray(input)) {
    for (const item of input) queue.push({ value: item, depth: 0 });
  } else if (typeof input === 'string') {
    queue.push({ value: input, depth: 0 });
  } else if (input === null || input === undefined) {
    return { tags: [], warnings };
  } else {
    // Non-string scalars (numbers, booleans): coerce — tags are strings.
    queue.push({ value: String(input), depth: 0 });
  }

  let guard = 0;
  while (queue.length > 0) {
    if (++guard > 10000) break; // pathological input fuse
    const { value, depth } = queue.shift()!;

    if (Array.isArray(value)) {
      for (const item of value) queue.push({ value: item, depth });
      continue;
    }
    if (typeof value !== 'string') {
      queue.push({ value: String(value), depth });
      continue;
    }

    let s = value.trim();
    if (s === '') continue; // §4.1: drop empties

    // §4.2a: unwrap JSON-stringified arrays/objects (bounded)
    if (depth < MAX_JSON_UNWRAP_DEPTH) {
      const parsed = parseMaybeJsonArray(s);
      if (parsed) {
        const inner = parsed.value;
        if (Array.isArray(inner)) {
          for (const item of inner) queue.push({ value: item, depth: depth + 1 });
          continue;
        }
        if (inner !== null && typeof inner === 'object') {
          for (const [k, v] of Object.entries(inner as Record<string, unknown>)) {
            queue.push({ value: v, depth: depth + 1 });
            queue.push({ value: k, depth: depth + 1 });
          }
          continue;
        }
        // JSON scalar (e.g. a quoted string) — fall through and clean it.
      }
    }

    // §4.2b: split comma-joined lists into multiple tags.
    const parts = s.includes(',') ? s.split(',') : [s];
    for (const part of parts) {
      let t = part.trim();
      if (t === '') continue;

      // §4.2c: strip bracket/quote/brace artifacts (defect residue).
      const stripped = t.replace(STRIP_CHARS, '').trim();
      if (stripped !== t && stripped !== '') {
        warnings.push({ tag: t, reason: 'charset' });
      }
      t = stripped;
      if (t === '') continue;

      // §4.3: classify. Class R = to: address — lowercase. Class V = value.
      if (t.toLowerCase().startsWith('to:')) {
        // §4.2d: concatenated-address split-repair (Ruling 13 decision 3).
        const split = splitConcatenatedAddresses(t);
        if (split.concat) warnings.push({ tag: t, reason: 'concatenated-address' });
        for (const seg of split.segments) pushAddress(seg);
      } else {
        // Class V: case PRESERVED. Charset/length checked, WARN only.
        if (t.length > TAG_MAX_LENGTH) {
          warnings.push({ tag: t, reason: 'over-length' });
        } else if (!CLASS_V_CHARSET.test(t)) {
          warnings.push({ tag: t, reason: 'charset' });
        }
        push(t);
      }
    }
  }

  return { tags: out, warnings };
}
