/**
 * §8 census guard — card `ef1b5077` §8 ("census guard"), Ruling 17 F2
 * ("direct-DB writers documented as residual census tripwire").
 *
 * ── What this detects, precisely ───────────────────────────────────────────
 *
 * The write-side normalizer (`normalizeTags`, card §4) is applied on EVERY
 * POST and PATCH of `nebula.agent_records`, and is idempotent. Therefore:
 *
 *     a record whose tags are not a FIXED POINT of normalizeTags
 *     cannot have been written through the REST write path.
 *
 * That is the whole detection rule, and it is the reason this module IMPORTS
 * `normalizeTags` rather than restating the grammar. A parallel Python or TS
 * reimplementation of §4 would be a second source of truth that drifts the
 * moment §4 changes — precisely the failure Ruling 17 (`c16b625b`) condemns:
 * "a prose reference passes review and enforces nothing." Here, changing §4
 * automatically changes what the census considers a bypass. The check is
 * `normalizeTags([t]).tags !== [t]`, evaluated by the shipping normalizer.
 *
 * The character tests used to EXPLAIN a finding (which repair would have
 * applied) are cosmetic and deliberately not load-bearing: detection is the
 * normalizer's verdict, never a local rule.
 *
 * ── Why an epoch exists (and why the default is "no epoch") ─────────────────
 *
 * The normalizer is not deployed yet (#714 is an open draft). So right now
 * EVERY dirty record in the corpus is historical, and flagging them would be a
 * false accusation: they were written through REST before §4 existed. §4 is
 * write-side only — historical records are never rewritten.
 *
 * So a finding is only a VIOLATION relative to an epoch — the moment the
 * normalizer went live. With no epoch configured, this module reports a
 * BASELINE and asserts nothing. That default is the honest one: a guard that
 * fails on the accumulated past teaches people to ignore it.
 *
 * ── Non-goals ──────────────────────────────────────────────────────────────
 *
 * Unregistered Class R addresses (#693's reject scope) are NOT flagged here.
 * An unknown address can be perfectly well-formed — that is a policy question
 * with its own registry, not a write-path bypass. Conflating them would make
 * this guard noisy for a reason it cannot fix.
 */

import { normalizeTags } from './tagNormalizer';

/** Why a stored tag could not have come through the REST write path. */
export type CensusReason =
  | 'class-r-not-normalized'
  | 'class-v-not-normalized'
  | 'artifact-tag'
  | 'empty-tag'
  | 'duplicate-tag';

export interface CensusRecord {
  id: string;
  /** ISO string or epoch milliseconds — the list endpoint returns epoch ms. */
  createdAt: string | number;
  tags: unknown;
}

export interface CensusFinding {
  id: string;
  createdAtIso: string;
  reasons: CensusReason[];
  /** Stored tags that are not fixed points, as they sit in the row. */
  offendingTags: string[];
  /** What the write path would have stored instead. */
  normalizedForm: string[];
}

export interface CensusOptions {
  /**
   * ISO instant the write-side normalizer became effective. Findings at or
   * after this instant are VIOLATIONS; earlier ones are baseline. Omit/null
   * while the normalizer is undeployed — see module docstring.
   */
  epochIso?: string | null;
}

export interface CensusReport {
  scanned: number;
  /** Records bypassing the normalizer, regardless of epoch. */
  findings: CensusFinding[];
  /** Findings at/after the epoch — the only ones that may fail a run. */
  violations: CensusFinding[];
  /** Findings before the epoch: expected while §4 is write-side only. */
  baseline: CensusFinding[];
  epochIso: string | null;
  /** True when no epoch is set, so `violations` is empty by construction. */
  baselineOnly: boolean;
}

/** Characters the §4 repair set strips or splits on. Explanation only. */
const ARTIFACT_CHARS = /[,[\]{}"']/;

function isClassR(tag: string): boolean {
  return tag.toLowerCase().startsWith('to:');
}

/** Normalize an epoch-ms-or-ISO createdAt to a comparable ISO instant. */
export function createdAtToIso(createdAt: string | number): string {
  if (typeof createdAt === 'number') return new Date(createdAt).toISOString();
  // The list endpoint returns epoch ms as a JSON *number*, but be tolerant of
  // a stringified one rather than mis-parsing it as a year.
  if (/^\d+$/.test(createdAt)) return new Date(Number(createdAt)).toISOString();
  const parsed = Date.parse(createdAt);
  return Number.isNaN(parsed) ? createdAt : new Date(parsed).toISOString();
}

function classifyTag(tag: string): CensusReason {
  // Classification runs on the TRIMMED tag. An untrimmed `  to:DBA  ` is still
  // a Class R address, and §4 repairs it in the Class R branch — testing
  // `startsWith('to:')` on the raw string mislabels it as Class V purely
  // because of the leading spaces.
  const t = tag.trim();
  if (t === '') return 'empty-tag';
  if (ARTIFACT_CHARS.test(t)) return 'artifact-tag';
  return isClassR(t) ? 'class-r-not-normalized' : 'class-v-not-normalized';
}

/**
 * Classify one record. Returns null when the record is clean, i.e. its tags
 * are exactly what the REST write path would have stored.
 */
export function classifyRecord(record: CensusRecord): CensusFinding | null {
  const stored: string[] = Array.isArray(record.tags)
    ? (record.tags as unknown[]).filter((t): t is string => typeof t === 'string')
    : [];

  const offending: string[] = [];
  const reasons = new Set<CensusReason>();

  // Per-tag fixed-point test. Order-independent by construction: each tag is
  // judged on its own, so a legitimately split record (to:engineer +
  // to:engineer-ii) is clean even though neither tag alone reconstructs it.
  for (const tag of stored) {
    const single = normalizeTags([tag]).tags;
    if (single.length !== 1 || single[0] !== tag) {
      offending.push(tag);
      reasons.add(classifyTag(tag));
    }
  }

  // Duplicate test: the write path dedupes via a `seen` set, so a row holding
  // the same tag twice also cannot have come through REST.
  const uniqueStored = new Set(stored);
  if (uniqueStored.size !== stored.length) {
    reasons.add('duplicate-tag');
    for (const tag of uniqueStored) {
      if (stored.filter((t) => t === tag).length > 1) offending.push(tag);
    }
  }

  if (reasons.size === 0) return null;

  return {
    id: record.id,
    createdAtIso: createdAtToIso(record.createdAt),
    reasons: [...reasons].sort(),
    offendingTags: [...new Set(offending)],
    normalizedForm: normalizeTags(stored).tags,
  };
}

export function runCensus(records: CensusRecord[], options: CensusOptions = {}): CensusReport {
  const epochIso = options.epochIso ?? null;
  const epochMs = epochIso ? Date.parse(epochIso) : null;
  if (epochIso && Number.isNaN(epochMs as number)) {
    throw new Error(`tag census: epoch is not a parseable instant: ${epochIso}`);
  }

  const findings: CensusFinding[] = [];
  for (const record of records) {
    const finding = classifyRecord(record);
    if (finding) findings.push(finding);
  }
  findings.sort((a, b) => (a.createdAtIso < b.createdAtIso ? -1 : 1));

  const isViolation = (f: CensusFinding): boolean =>
    epochMs !== null && Date.parse(f.createdAtIso) >= epochMs;

  const violations = findings.filter(isViolation);
  return {
    scanned: records.length,
    findings,
    violations,
    baseline: findings.filter((f) => !isViolation(f)),
    epochIso,
    baselineOnly: epochMs === null,
  };
}

/** One-line human summary. Kept here so the runner stays presentational. */
export function summarizeCensus(report: CensusReport): string {
  const byReason = new Map<CensusReason, number>();
  for (const f of report.findings) {
    for (const r of f.reasons) byReason.set(r, (byReason.get(r) ?? 0) + 1);
  }
  const reasons = [...byReason.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([r, n]) => `${r}=${n}`)
    .join(' ');
  return (
    `scanned=${report.scanned} findings=${report.findings.length} ` +
    `violations=${report.violations.length} baseline=${report.baseline.length}` +
    (reasons ? ` [${reasons}]` : '')
  );
}