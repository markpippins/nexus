/**
 * Census flag surface and execution-envelope emission — CDLC A3 (+ the pure part of A4).
 *
 * Authority: `a5991156` (flag surface, union rule, fail-loud validation),
 * `6629b009` three-tier split, `dffa404e` (execution identity, DBA ruling),
 * `a27dc653` (naming: `census`, never `delta`), `c3d1c0a8` (Tier-3 shape).
 *
 * ## Scope, and what is deliberately NOT here
 *
 * IN: the flag surface, the Tier-2 marker decision, envelope construction, and the insert.
 * That is A3's acceptance criterion — *"100% of executions carry telemetry; sampling marker
 * present even when not sampled"* — plus the pure half of A4's flag surface.
 *
 * OUT: the post-action interview (separate follow-on record, `lib/census-interview.ts` per
 * Decision 22), the census *report* body (Tier 3, `lib/census-report.ts`), the classifier that
 * decides census categories, and the rollback/revise workflow. None of those are A3.
 *
 * ## Flags are orthogonal and union, not ranked
 *
 * `--census` is the master switch; `--census-rate N/M` owns the rate trigger only;
 * `--census-on-review` owns the review trigger only. Because each flag owns a distinct trigger
 * source, no combination can conflict and there is no precedence to remember. The census fires
 * if `(selected by N/M)` **OR** `(a review event occurred AND --census-on-review)`.
 *
 * ## Fail loud, never silently inert
 *
 * An inert flag is a configuration defect, not a harmless no-op. A sampling rate that does not
 * take effect is an undetectable loss of coverage that A5 would then read as real data — so
 * `--census-rate` without `--census` is an error, and so is any malformed rate. This is the same
 * fail-closed reasoning that governs the CI and attestation gates.
 *
 * ## Sampling is DETERMINISTIC, never random
 *
 * `1/N` sampling keyed on the execution id means the same execution always yields the same
 * decision, so a re-run, a retry, or a replay reaches the same verdict. `Math.random()` would
 * make the census unauditable: you could not tell a non-sampled execution from a re-roll.
 */

import { randomUUID } from "crypto";
import {
  validateExecutionEnvelope,
  type CensusMarker,
  type ExecutionEnvelope,
  type ExecutionFrame,
  type ExecutionIdentity,
  type ExecutionOutcome,
  type ExecutionSubject,
} from "./execution-identity.ts";

export const CENSUS_FLAG_NAMES = [
  "--census",
  "--census-rate",
  "--census-on-review",
] as const;

const RATE_RE = /^(\d+)\/(\d+)$/;

export interface CensusFlags {
  /** Master switch. Absent means the census is disabled for this walk. */
  census: boolean;
  /** `N/M`; null means master-switch-only, which is 1/1. */
  rate: string | null;
  /** Adds the review-event trigger. */
  onReview: boolean;
}

/**
 * Census environment variables, and the only recognised ones.
 *
 * A typo'd variable is the same class of defect as a typo'd flag: the census silently does
 * not run. `censusArgvFromEnv` therefore reports unrecognised `CENSUS_*` keys as errors
 * instead of ignoring them, so `CENSUS_RATTE=1/20` cannot masquerade as configuration.
 */
export const CENSUS_ENV_KEYS = [
  "CENSUS_ENABLED",
  "CENSUS_RATE",
  "CENSUS_ON_REVIEW",
] as const;

export interface CensusEnvResult {
  ok: boolean;
  argv: string[];
  errors: string[];
}

const TRUTHY = new Set(["1", "true", "yes", "on"]);

/**
 * Synthesise the census argv from environment variables.
 *
 * ## Why synthesise argv rather than read the values directly
 *
 * `parseCensusFlags` deliberately takes **raw argv** so it can detect a flag that was dropped
 * on the way in — the silently-inert case the ruling targets. If the env path bypassed it and
 * read `CENSUS_RATE` itself, the fail-loud rule ("rate without the master switch is an error")
 * would have two implementations that could disagree. There is one parser; the environment
 * only decides which tokens to hand it.
 *
 * Fail-loud stays intact: `CENSUS_RATE` set without `CENSUS_ENABLED` produces argv that
 * `parseCensusFlags` rejects, rather than a rate that quietly never takes effect.
 */
export function censusArgvFromEnv(env: Record<string, string | undefined>): CensusEnvResult {
  const argv: string[] = [];
  const errors: string[] = [];

  for (const [key, value] of Object.entries(env)) {
    if (!key.startsWith("CENSUS_") || !value) continue;
    if (!(CENSUS_ENV_KEYS as readonly string[]).includes(key)) {
      errors.push(
        `unrecognised census environment variable ${key}. It would be silently ignored, so ` +
          "the census would not run the way it was configured -- fail instead.",
      );
    }
  }

  const enabled = env.CENSUS_ENABLED;
  if (enabled !== undefined && enabled !== "") {
    if (TRUTHY.has(String(enabled).toLowerCase())) argv.push("--census");
    else if (!["0", "false", "no", "off"].includes(String(enabled).toLowerCase())) {
      errors.push(
        `CENSUS_ENABLED must be a boolean-ish value (1/0, true/false, yes/no, on/off); got "${enabled}"`,
      );
    }
  }

  const rate = env.CENSUS_RATE;
  if (rate !== undefined && rate !== "") argv.push("--census-rate", String(rate));

  const onReview = env.CENSUS_ON_REVIEW;
  if (onReview !== undefined && onReview !== "") {
    if (TRUTHY.has(String(onReview).toLowerCase())) argv.push("--census-on-review");
    else if (!["0", "false", "no", "off"].includes(String(onReview).toLowerCase())) {
      errors.push(
        `CENSUS_ON_REVIEW must be a boolean-ish value (1/0, true/false, yes/no, on/off); got "${onReview}"`,
      );
    }
  }

  return { ok: errors.length === 0, argv, errors };
}

export interface FlagParseResult {
  ok: boolean;
  flags: CensusFlags;
  errors: string[];
}

/**
 * Parse and validate the census flag surface.
 *
 * Deliberately handed raw argv rather than a typed options object: a parser that receives
 * already-typed flags cannot detect a flag that was *dropped* upstream, which is precisely the
 * silently-inert case the ruling targets.
 */
export function parseCensusFlags(args: readonly string[]): FlagParseResult {
  const errors: string[] = [];
  const seen = new Set<string>();
  const flags: CensusFlags = { census: false, rate: null, onReview: false };

  // Walk the ORIGINAL argv, not a filtered list. A filtered list drops the bare value token
  // of a space-separated `--census-rate 1/20`, so the value has to be read from `args` at the
  // same index — the bug where `--census-rate 1/20` reported "requires an N/M value".
  for (let i = 0; i < args.length; i++) {
    const token = args[i];
    if (!token.startsWith("--census")) continue;
    const [name, inlineValue] = token.includes("=")
      ? [token.slice(0, token.indexOf("=")), token.slice(token.indexOf("=") + 1)]
      : [token, undefined];

    if (seen.has(name)) errors.push(`${name} was passed more than once`);
    seen.add(name);

    switch (name) {
      case "--census":
        if (inlineValue !== undefined) errors.push("--census takes no value");
        flags.census = true;
        break;
      case "--census-on-review":
        if (inlineValue !== undefined) errors.push("--census-on-review takes no value");
        flags.onReview = true;
        break;
      case "--census-rate": {
        if (flags.rate !== null) {
          errors.push("--census-rate was passed more than once");
          break;
        }
        // Either `--census-rate 1/20` or `--census-rate=1/20`.
        const value = inlineValue !== undefined ? inlineValue : args[i + 1];
        if (value === undefined || value.startsWith("--")) {
          errors.push("--census-rate requires an N/M value");
          break;
        }
        if (inlineValue === undefined) i++;
        flags.rate = value;
        break;
      }
      default:
        // A near-miss like --census-rat is far more likely a typo than a different flag.
        errors.push(
          `unrecognised census flag "${name}". This walk would have run a census that was ` +
            "not the one you asked for, which is the silently-inert case -- fail instead.",
        );
    }
  }

  // Fail-loud validation (a5991156).
  if (flags.rate !== null && !flags.census) {
    errors.push(
      "--census-rate was passed without --census; the sampling rate would never take effect, " +
        "and A5 would read the lost coverage as real data",
    );
  }
  if (flags.onReview && !flags.census) {
    errors.push("--census-on-review was passed without --census; the review trigger would never fire");
  }
  if (flags.rate !== null) {
    const match = RATE_RE.exec(flags.rate);
    if (!match) {
      errors.push(`--census-rate must be N/M (e.g. 1/20); got "${flags.rate}"`);
    } else {
      const numerator = Number(match[1]);
      const denominator = Number(match[2]);
      if (denominator < 1) errors.push("--census-rate denominator must be >= 1");
      if (numerator > denominator) errors.push("--census-rate must satisfy N <= M");
    }
  }

  return { ok: errors.length === 0, flags, errors };
}

export interface SamplingInput {
  flags: CensusFlags;
  /** The execution id the decision is keyed on — see the determinism note above. */
  executionId: string;
  /** Whether a review event occurred during this walk. */
  reviewEvent: boolean;
}

export interface SamplingDecision {
  marker: CensusMarker;
  /** Non-null exactly when sampled. The join key to the single Tier-3 report. */
  censusId: string | null;
  /** Why, for the record. Never inferred downstream. */
  reason: "census-disabled" | "master-switch-only" | "rate-selected" | "review-trigger" | "rate-missed";
}

/** A stable 32-bit hash of the execution id — no crypto dependency, and stable across runs. */
function stableHash(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/**
 * Decide the Tier-2 marker for one execution.
 *
 * Deterministic in `executionId`, so the same execution always reaches the same verdict. That is
 * what makes a re-run, retry or replay auditable instead of a fresh coin-flip.
 */
export function decideCensusMarker(input: SamplingInput): SamplingDecision {
  const { flags, executionId, reviewEvent } = input;

  if (!flags.census) {
    return {
      marker: { enabled: false, sampled: false, rate: null },
      censusId: null,
      reason: "census-disabled",
    };
  }

  // Master switch alone means 1/1: every execution.
  const rate = flags.rate ?? "1/1";
  if (rate === "1/1") {
    return {
      marker: { enabled: true, sampled: true, rate },
      censusId: randomUUID(),
      reason: "master-switch-only",
    };
  }

  const match = RATE_RE.exec(rate);
  const numerator = match ? Number(match[1]) : 1;
  const denominator = match ? Number(match[2]) : 1;

  // Hash modulo 1, so a `1/N` rate selects exactly one execution in N.
  const selected = stableHash(executionId) % denominator < numerator;

  if (selected) {
    return {
      marker: { enabled: true, sampled: true, rate },
      censusId: randomUUID(),
      reason: "rate-selected",
    };
  }

  if (reviewEvent && flags.onReview) {
    // The union rule: a review event fires the census even when the rate missed.
    return {
      marker: { enabled: true, sampled: true, rate },
      censusId: randomUUID(),
      reason: "review-trigger",
    };
  }

  return {
    marker: { enabled: true, sampled: false, rate },
    censusId: null,
    // Distinct from rate-selected: this execution was evaluated and NOT chosen. Collapsing
    // the two would make "not observed" indistinguishable from "observed and declined", which
    // is the distinction A5's denominator depends on.
    reason: "rate-missed",
  };
}

export interface BuildEnvelopeInput {
  sessionId?: string | null;
  sourceNamespace: string;
  ticketId?: string | null;
  workItemId?: string | null;
  doctrineSnapshotId: string;
  procedureCardSetHash?: string | null;
  outcomeStatus: string;
  outcomeDetail?: string | null;
  /** Supplied by tests or by a caller replaying a known execution; minted when absent. */
  executionId?: string;
  decision: SamplingDecision;
  executedByRole: string;
  executedByModel: string;
  createdAt?: Date;
}

/**
 * Build the Tier-1/Tier-2 envelope for one execution.
 *
 * `session_id` is passed through opaquely and is never parsed or normalised: DBA ruling
 * `dffa404e` condition 2 makes it a reference, and condition 3 forbids it carrying a second
 * concept. A uuid-shaped value is rejected by `validateExecutionEnvelope`, and I do not
 * pre-empt that here — one place owns the rule.
 */
export function buildExecutionEnvelope(input: BuildEnvelopeInput): ExecutionEnvelope {
  const executionId = input.executionId ?? randomUUID();
  const identity: ExecutionIdentity = {
    schema_version: 1,
    execution_id: executionId,
    session_id: input.sessionId ?? null,
    census_id: input.decision.censusId,
  };
  const frame: ExecutionFrame = {
    doctrine_snapshot_id: input.doctrineSnapshotId,
    ...(input.procedureCardSetHash ? { procedure_card_set_hash: input.procedureCardSetHash } : {}),
  };
  const subject: ExecutionSubject = {
    source_namespace: input.sourceNamespace,
    ...(input.ticketId ? { ticket_id: input.ticketId } : {}),
    ...(input.workItemId ? { work_item_id: input.workItemId } : {}),
  };
  const outcome: ExecutionOutcome = {
    status: input.outcomeStatus,
    ...(input.outcomeDetail ? { detail: input.outcomeDetail } : {}),
  };
  return { execution: identity, census: input.decision.marker, subject, frame, outcome };
}

/**
 * The INSERT for an envelope, plus the parameters, in the exact column order of
 * `nebula.executions` as applied by migration 068.
 *
 * Returned as a parameterised statement rather than interpolated SQL: `session_id` is caller-
 * supplied free text and `census_rate` comes from a flag, so neither may be string-concatenated.
 */
export function toExecutionInsert(
  envelope: ExecutionEnvelope,
  createdAt: Date = new Date(),
): { text: string; values: unknown[] } {
  return {
    text:
      "INSERT INTO nebula.executions (execution_id, session_id, census_enabled, census_sampled, " +
      "census_id, census_rate, source_namespace, ticket_id, work_item_id, doctrine_snapshot_id, " +
      "procedure_card_set_hash, outcome_status, outcome_detail, created_at) " +
      "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
    values: [
      envelope.execution.execution_id,
      envelope.execution.session_id,
      envelope.census.enabled,
      envelope.census.sampled,
      envelope.execution.census_id,
      envelope.census.rate,
      envelope.subject.source_namespace,
      envelope.subject.ticket_id ?? null,
      envelope.subject.work_item_id ?? null,
      envelope.frame.doctrine_snapshot_id,
      envelope.frame.procedure_card_set_hash ?? null,
      envelope.outcome.status,
      envelope.outcome.detail ?? null,
      createdAt.toISOString(),
    ],
  };
}

export interface EmissionResult {
  ok: boolean;
  errors: string[];
  envelope: ExecutionEnvelope | null;
  decision: SamplingDecision | null;
  insert: { text: string; values: unknown[] } | null;
}

/**
 * End-to-end emission: flags -> sampling decision -> envelope -> validated INSERT.
 *
 * The envelope is validated before the statement is produced, so a malformed one cannot reach the
 * database as a row that only fails on a constraint. The table is append-only (migration 068), so
 * a bad row is not repairable by UPDATE — the check has to happen before the write.
 */
export function prepareExecutionEmission(
  args: readonly string[],
  context: Omit<BuildEnvelopeInput, "decision">,
  options: { reviewEvent?: boolean; executionId?: string } = {},
): EmissionResult {
  const parsed = parseCensusFlags(args);
  if (!parsed.ok) {
    return { ok: false, errors: parsed.errors, envelope: null, decision: null, insert: null };
  }

  const executionId = options.executionId ?? randomUUID();
  const decision = decideCensusMarker({
    flags: parsed.flags,
    executionId,
    reviewEvent: Boolean(options.reviewEvent),
  });

  const envelope = buildExecutionEnvelope({ ...context, executionId, decision });
  const validation = validateExecutionEnvelope(envelope);
  if (!validation.ok) {
    return { ok: false, errors: validation.errors, envelope, decision, insert: null };
  }

  return { ok: true, errors: [], envelope, decision, insert: toExecutionInsert(envelope) };
}
