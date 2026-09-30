/**
 * Content-addressed doctrine snapshots — TypeScript port of the canonical recipe.
 *
 * Authority: architect **Decision 28** (`85c6d979`), which rules that the
 * `python/peb-kernel/src/peb_kernel/doctrine.py` recipe is the canonical contract
 * and that a second implementation is unacceptable unless pinned by a shared
 * cross-language fixture (`bin/fixtures/doctrine-snapshot-vectors.json`) read by
 * BOTH the python and the TS suites.
 *
 * ## ⚠ Read this before "fixing" the key order
 *
 * Decision 28 Ruling 2 describes the payload key order as
 * `schema_version, system_prompt_hash, bootstrap_hash, active_procedure_cards`.
 * That is the *field declaration* order, **not** the serialization order.
 * The canonical implementation uses `json.dumps(..., sort_keys=True)`, so the
 * bytes that are actually hashed are ALPHABETICAL:
 *
 *   active_procedure_cards, bootstrap_hash, schema_version, system_prompt_hash
 *
 * Serialising in the field order would produce a different digest than python,
 * which would silently split the address space: the same doctrine would resolve
 * to two different frames depending on which language wrote the row, and every
 * B1/B3 cohort query would be quietly wrong. The fixture catches exactly this,
 * which is why it is mandatory rather than advisory.
 */

import { createHash } from "crypto";

/** Decision 28 Ruling 2.1: the contract version. Bump on any change to key order, hashing, or inputs. */
export const SNAPSHOT_SCHEMA_VERSION = 1;

/** Decision 28 Ruling 3: `sha256:` prefix is mandatory; bare hex is invalid. */
export const SHA256_RE = /^sha256:[0-9a-f]{64}$/;

/**
 * Canonical JSON, byte-identical to python `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`.
 *
 * The three separators matter and are not interchangeable:
 * - `sort_keys`      — recursive key sort by code point.
 * - compact separators — no whitespace at all.
 * - `ensure_ascii=False` — non-ASCII stays literal, so the UTF-8 bytes hashed match python's.
 */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("canonicalJson: non-finite number");
    // JSON.stringify renders integers without a trailing ".0", matching python.
    return JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((v) => canonicalJson(v)).join(",")}]`;
  if (typeof value === "object") {
    // Recursive sort, matching python's sort_keys (applies at every nesting level).
    const keys = Object.keys(value as Record<string, unknown>).sort();
    const body = keys
      .map((k) => `${JSON.stringify(k)}:${canonicalJson((value as Record<string, unknown>)[k])}`)
      .join(",");
    return `{${body}}`;
  }
  throw new Error(`canonicalJson: unsupported type ${typeof value}`);
}

/**
 * Hash content, accepting an already-normalised `sha256:` reference.
 *
 * Pass-through of an existing reference is load-bearing: `build_doctrine_snapshot`
 * may be called with already-hashed components (as `from_dict` does), and
 * re-hashing `"sha256:..."` would change the address and break idempotence.
 */
export function contentHash(value: unknown): string {
  if (typeof value === "string" && SHA256_RE.test(value)) return value;
  const raw = typeof value === "string" ? value : canonicalJson(value);
  return `sha256:${createHash("sha256").update(raw, "utf8").digest("hex")}`;
}

/**
 * Normalise procedure cards: hash by FULL CONTENT, then sort and dedupe.
 *
 * Content, not slug — so an edit to a card changes the address by design
 * (Decision 28 Ruling 2.3). A bare string is taken as the card's content; an
 * object is canonicalised, matching python's `_normalize_cards`.
 */
export function normalizeCards(cards: readonly unknown[]): string[] {
  const hashes = new Set<string>();
  for (const card of cards) {
    if (typeof card === "string") {
      hashes.add(contentHash(card));
    } else if (card && typeof card === "object" && !Array.isArray(card)) {
      hashes.add(contentHash(card));
    } else {
      throw new Error("active_procedure_cards must be a sequence of card contents");
    }
  }
  return [...hashes].sort();
}

export interface DoctrineSnapshot {
  schema_version: typeof SNAPSHOT_SCHEMA_VERSION;
  snapshot_id: string;
  system_prompt_hash: string;
  bootstrap_hash: string;
  active_procedure_cards: string[];
  /** Decision 28 "Also ruled": the card-set hash, denormalised for card-level cohort filtering. */
  procedure_card_set_hash: string;
}

export interface BuildSnapshotInput {
  systemPrompt: string;
  /**
   * Decision 28 Ruling 3: the A1-vetted governing text — the ratified frame that
   * governs the walk, NOT the mutable latest in Redis.
   */
  bootstrap: string;
  activeProcedureCards: readonly unknown[];
}

/**
 * Build the content-addressed doctrine snapshot.
 *
 * Session identity, wall-clock, and execution identity are intentionally excluded,
 * so identical doctrine sets share one address (module docstring, doctrine.py:1-7).
 */
export function buildDoctrineSnapshot(input: BuildSnapshotInput): DoctrineSnapshot {
  if (typeof input.systemPrompt !== "string" || typeof input.bootstrap !== "string") {
    throw new TypeError("systemPrompt and bootstrap must be strings");
  }
  const systemPromptHash = contentHash(input.systemPrompt);
  const bootstrapHash = contentHash(input.bootstrap);
  const cards = normalizeCards(input.activeProcedureCards);

  // Key insertion order here is irrelevant to the digest: canonicalJson sorts.
  // It is written in the field order Decision 2.2 names, for readability.
  const payload = {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    system_prompt_hash: systemPromptHash,
    bootstrap_hash: bootstrapHash,
    active_procedure_cards: cards,
  };

  return {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    snapshot_id: contentHash(payload),
    system_prompt_hash: systemPromptHash,
    bootstrap_hash: bootstrapHash,
    active_procedure_cards: cards,
    // Hash of the sorted/deduped card-hash set, so card-level cohort filtering
    // works without re-parsing the payload.
    procedure_card_set_hash: contentHash(cards),
  };
}
