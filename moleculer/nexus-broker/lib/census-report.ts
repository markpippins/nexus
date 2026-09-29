/**
 * Census report record shape and validator — CDLC A2b.
 *
 * A census is a sampled, classified enumeration of context gaps with an interview
 * follow-up. The token `delta` is B-series only (`CheckpointDelta`, `storage: "delta"`,
 * `base_version`, `delta_steps`); see architect decision `a27dc653`. Nothing in this
 * module may introduce a `delta` key.
 *
 * Authority: ratification `c3d1c0a8` (record shape + venue, Q1-Q7), category enum and
 * precedence `a5991156`, naming `a27dc653`, tier split `6629b009`.
 *
 * Two standing constraints from the ratification:
 *
 *  1. **Validates; does not re-derive.** The seven-name vocabulary and the precedence
 *     order live in A4's classifier. This module rejects malformed payloads and checks
 *     the closed vocabularies. It never applies precedence itself, because two
 *     components that each apply precedence will eventually disagree about UNUSED.
 *     `CENSUS_CATEGORY_PRECEDENCE` is exported so A4 has one source of order, not a
 *     second implementation.
 *  2. **Interview outcome shape — Architect Decision 22 (2026-09-29).** The roundtable
 *     ruled: interviewer is NOT adjudicator, and no one adjudicates their own
 *     participation. That makes two things structural rather than advisory, and both are
 *     enforced below: the record must distinguish `self_report` from
 *     `independent_interrogation`, and it must carry interviewer identity. The v1 *scope*
 *     (context-only vs reasoning) is parked with the Architect and is deliberately NOT
 *     encoded here — this module validates shape, it does not settle policy.
 */

export const CENSUS_SCHEMA_VERSION = 1;

/** The seven categories from `a5991156`, as the shared vocabulary. */
export const CENSUS_CATEGORIES = [
  "MISSING",
  "INCORRECT",
  "OVERRIDDEN",
  "REDUNDANT",
  "UNUSED",
  "RECOMMENDED",
  "EMERGENT",
] as const;

export type CensusCategory = (typeof CENSUS_CATEGORIES)[number];

/**
 * Precedence, first match wins — exactly one label per finding, which is what A5's
 * "3 consecutive same-gap" rule needs. Ordered from "doctrine is failing hardest" to
 * "doctrine could be better"; deficit beats suggestion, so an absent-but-harmful card is
 * MISSING rather than RECOMMENDED.
 *
 * EMERGENT is absent by construction: it is the provenance axis, and applies only when
 * none of the other six do. The orthogonal axis is carried by `anticipated: boolean`.
 */
export const CENSUS_CATEGORY_PRECEDENCE = [
  "MISSING",
  "INCORRECT",
  "OVERRIDDEN",
  "REDUNDANT",
  "UNUSED",
  "RECOMMENDED",
] as const;

/** `--census-rate` owns the rate trigger, `--census-on-review` the review trigger. */
export const CENSUS_TRIGGERS = ["rate", "on-review"] as const;

export type CensusTrigger = (typeof CENSUS_TRIGGERS)[number];

/**
 * Closed evidence vocabulary from `c3d1c0a8` Q4. Extensible by ruling, not by caller —
 * an open set would let A4 invent vocabulary and destroy the property that the categories
 * are tight enough to classify against.
 */
/** Decision 22 vocabularies, exported so A4 and the read path share one source. */
export const CENSUS_INTERVIEW_EVIDENCE_CLASSES = [
  "self_report",
  "independent_interrogation",
] as const;
export const CENSUS_INTERVIEW_STATUSES = ["pending", "conducted", "declined"] as const;

export const CENSUS_EVIDENCE_KINDS = [
  "card_absent",
  "card_present_unconsumed",
  "card_content_conflict",
  "card_superseded",
  "card_duplicate",
  "card_unanticipated",
  "observation",
] as const;

export type CensusEvidenceKind = (typeof CENSUS_EVIDENCE_KINDS)[number];

/**
 * Advisory only — the validator does NOT enforce this mapping, because doing so would
 * re-derive classification inside A2b. Recorded because it is a non-obvious consequence of
 * the ruling: six categories have a dedicated evidence kind and RECOMMENDED does not, so
 * `observation` is its catch-all. Useful to A4 as a starting point, not a contract.
 */
export const CENSUS_EVIDENCE_KIND_HINTS: Readonly<Partial<Record<CensusCategory, CensusEvidenceKind>>> = {
  MISSING: "card_absent",
  INCORRECT: "card_content_conflict",
  OVERRIDDEN: "card_superseded",
  REDUNDANT: "card_duplicate",
  UNUSED: "card_present_unconsumed",
  EMERGENT: "card_unanticipated",
};

export interface CensusFindingCard {
  /** keychain `InstanceManifest.asset_id` — the unit of classification. */
  asset_id: string;
  instance_id: string;
  record_type: string;
  record_id: string;
  version: number;
}

export interface CensusEvidence {
  kind: CensusEvidenceKind;
  ref: string;
  note?: string;
}

export interface CensusFinding {
  card: CensusFindingCard;
  /** Exactly one label, already resolved by A4's classifier. */
  category: CensusCategory;
  /** The orthogonal provenance axis from `a5991156`. */
  anticipated: boolean;
  evidence: CensusEvidence[];
  note?: string;
}

export type CensusRecommendedUpdateKind = "new-card" | "amend-card" | "retire-card" | "no-action";

export interface CensusRecommendedUpdate {
  kind: CensusRecommendedUpdateKind;
  target_asset_id: string | null;
  rationale: string;
  /** Makes A5's "3 consecutive same-gap" auditable after the fact rather than trusted. */
  supporting_census_ids: string[];
}

export interface CensusEnvelope {
  schema_version: typeof CENSUS_SCHEMA_VERSION;
  census_id: string;
  execution_id: string;
  /** Tier-3 rows exist only for sampled executions; unsampled carries no row at all. */
  sampled: true;
  rate: string | null;
  /**
   * Non-empty exactly when sampled is true. There is deliberately NO singular `trigger`
   * key: the union rule can fire both sources at once, and persisting a derived scalar
   * beside its own source array is the drift class this series already produced twice.
   * Derive a primary at query time if one is needed.
   */
  triggers: CensusTrigger[];
  /** Session identity is (role, model, session_id); role alone is a partial key. */
  executed_by_role: string;
  executed_by_model: string;
}

export interface CensusFrame {
  doctrine_snapshot_id: string;
  procedure_card_set_hash?: string;
}

/**
 * Decision 22: the deciding fact in the post-action path is not the wording of a
 * question but WHO IS ASKING. So the evidence class is a required discriminator, not a
 * note.
 *
 *   self_report               the executing agent describing its own run
 *   independent_interrogation a third party, who therefore cannot be the executing agent
 */
export type CensusInterviewEvidenceClass = (typeof CENSUS_INTERVIEW_EVIDENCE_CLASSES)[number];

/**
 * `declined` is a first-class outcome, not a failure. Decision 22: a refusal is an
 * exercise of the executing agent's agency, and is neither grounds to reject a census
 * report nor grounds to validate it. It goes into the record and gets weighed.
 */
export type CensusInterviewStatus = (typeof CENSUS_INTERVIEW_STATUSES)[number];

export interface CensusInterviewer {
  role: string;
  /** Named `model_id` because `model` is a reserved TypeSpec keyword. */
  model_id?: string | null;
  /** True when the interviewer is not the executing agent. Never inferred; always stated. */
  independent_of_executor: boolean;
}

export interface CensusInterview {
  status: CensusInterviewStatus;
  /** Required when conducted; absent otherwise. */
  evidence_class?: CensusInterviewEvidenceClass;
  /** Required when conducted; absent otherwise. */
  interviewer?: CensusInterviewer;
  /** Free-text account of a decline. Never interpreted by this module. */
  decline_reason?: string;
}

export interface CensusReportMetadata {
  census: CensusEnvelope;
  frame: CensusFrame;
  findings: CensusFinding[];
  /** Shape per Architect Decision 22. */
  interview: CensusInterview;
  recommended_updates: CensusRecommendedUpdate[];
}

export interface CensusValidationResult {
  ok: boolean;
  errors: string[];
}

const RATE_RE = /^(\d+)\/(\d+)$/;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/**
 * Naming guard (`a27dc653`): no `delta` key anywhere in a census payload. In this
 * subsystem `delta` means checkpoint storage strategy, full stop — allowing it here is
 * exactly the collision the rename was made to end.
 */
function collectDeltaKeys(value: unknown, path: string, out: string[]): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectDeltaKeys(item, `${path}[${index}]`, out));
    return;
  }
  if (!isPlainObject(value)) return;
  for (const [key, child] of Object.entries(value)) {
    const childPath = path ? `${path}.${key}` : key;
    if (key === "delta" || key === "delta_steps" || key === "base_version") {
      out.push(`${childPath} is a B-series checkpoint key and is forbidden in a census payload`);
    }
    collectDeltaKeys(child, childPath, out);
  }
}

/**
 * Validate a census `metadata` payload. Collects every error rather than throwing on the
 * first, so a caller (A4) can report a complete rejection reason in one pass.
 *
 * Deliberately NOT enforced here: the category/evidence correspondence, the precedence
 * order, and whether a category is correct. Those belong to A4's classifier.
 */
export function validateCensusReportMetadata(metadata: unknown): CensusValidationResult {
  const errors: string[] = [];
  collectDeltaKeys(metadata, "metadata", errors);

  if (!isPlainObject(metadata)) {
    return { ok: false, errors: [...errors, "metadata must be an object"] };
  }

  const census = metadata.census;
  if (!isPlainObject(census)) {
    errors.push("metadata.census is required and must be an object");
  } else {
    if (census.schema_version !== CENSUS_SCHEMA_VERSION) {
      errors.push(`metadata.census.schema_version must be ${CENSUS_SCHEMA_VERSION}`);
    }
    if (!isNonEmptyString(census.census_id)) errors.push("metadata.census.census_id is required");
    if (!isNonEmptyString(census.execution_id)) errors.push("metadata.census.execution_id is required");
    if (census.sampled !== true) {
      errors.push("metadata.census.sampled must be true; unsampled executions carry no Tier-3 row");
    }
    if ("trigger" in census) {
      errors.push('metadata.census.trigger is forbidden; carry the array as "triggers" only');
    }
    if (!Array.isArray(census.triggers) || census.triggers.length === 0) {
      errors.push("metadata.census.triggers must be a non-empty array");
    } else {
      for (const trigger of census.triggers) {
        if (!(CENSUS_TRIGGERS as readonly string[]).includes(String(trigger))) {
          errors.push(`metadata.census.triggers contains an unknown trigger: ${String(trigger)}`);
        }
      }
      if (new Set(census.triggers).size !== census.triggers.length) {
        errors.push("metadata.census.triggers must not contain duplicates");
      }
    }
    if (census.rate !== null) {
      if (!isNonEmptyString(census.rate)) {
        errors.push("metadata.census.rate must be an \"N/M\" string or null");
      } else {
        const match = RATE_RE.exec(census.rate);
        if (!match) {
          errors.push(`metadata.census.rate must match N/M; got ${census.rate}`);
        } else {
          const numerator = Number(match[1]);
          const denominator = Number(match[2]);
          if (denominator < 1) errors.push("metadata.census.rate denominator must be >= 1");
          if (numerator > denominator) errors.push("metadata.census.rate must satisfy N <= M");
        }
      }
    }
    if (!isNonEmptyString(census.executed_by_role)) {
      errors.push("metadata.census.executed_by_role is required");
    }
    if (!isNonEmptyString(census.executed_by_model)) {
      errors.push("metadata.census.executed_by_model is required");
    }
  }

  const frame = metadata.frame;
  if (!isPlainObject(frame)) {
    errors.push("metadata.frame is required and must be an object");
  } else {
    if (!isNonEmptyString(frame.doctrine_snapshot_id)) {
      errors.push("metadata.frame.doctrine_snapshot_id is required (B1/B3 provenance frame)");
    }
    if ("procedure_card_set_hash" in frame && frame.procedure_card_set_hash !== null
      && !isNonEmptyString(frame.procedure_card_set_hash)) {
      errors.push("metadata.frame.procedure_card_set_hash must be a string when present");
    }
  }

  const findings = metadata.findings;
  if (!Array.isArray(findings)) {
    errors.push("metadata.findings must be an array (a sampled execution always has a row, even with none)");
  } else {
    findings.forEach((finding, index) => {
      const at = `metadata.findings[${index}]`;
      if (!isPlainObject(finding)) {
        errors.push(`${at} must be an object`);
        return;
      }
      const card = finding.card;
      if (!isPlainObject(card) || !isNonEmptyString(card.asset_id)) {
        errors.push(`${at}.card.asset_id is required (the unit of classification)`);
      } else {
        for (const key of ["instance_id", "record_type", "record_id"] as const) {
          if (!isNonEmptyString(card[key])) errors.push(`${at}.card.${key} is required`);
        }
        if (typeof card.version !== "number" || !Number.isInteger(card.version)) {
          errors.push(`${at}.card.version must be an integer`);
        }
      }
      if (!(CENSUS_CATEGORIES as readonly string[]).includes(String(finding.category))) {
        errors.push(`${at}.category must be one of the seven categories`);
      }
      if (typeof finding.anticipated !== "boolean") {
        errors.push(`${at}.anticipated must be a boolean (the orthogonal provenance axis)`);
      }
      if (!Array.isArray(finding.evidence) || finding.evidence.length === 0) {
        errors.push(`${at}.evidence must be a non-empty array`);
      } else {
        finding.evidence.forEach((evidence, evidenceIndex) => {
          const evidenceAt = `${at}.evidence[${evidenceIndex}]`;
          if (!isPlainObject(evidence)) {
            errors.push(`${evidenceAt} must be an object`);
            return;
          }
          if (!(CENSUS_EVIDENCE_KINDS as readonly string[]).includes(String(evidence.kind))) {
            errors.push(`${evidenceAt}.kind is outside the closed evidence vocabulary`);
          }
          if (!isNonEmptyString(evidence.ref)) errors.push(`${evidenceAt}.ref is required`);
        });
      }
    });
  }

  const interview = metadata.interview;
  if (!isPlainObject(interview)) {
    errors.push("metadata.interview is required and must be an object");
  } else {
    const allowedKeys = ["status", "evidence_class", "interviewer", "decline_reason"];
    for (const key of Object.keys(interview)) {
      if (!allowedKeys.includes(key)) {
        errors.push(
          `metadata.interview.${key} is not part of the Decision 22 shape ` +
            `(${allowedKeys.join(", ")}); the outcome lands as a follow-on record ` +
            "referencing census_id",
        );
      }
    }
    if (!(CENSUS_INTERVIEW_STATUSES as readonly string[]).includes(String(interview.status))) {
      errors.push(
        `metadata.interview.status must be one of ${CENSUS_INTERVIEW_STATUSES.join(" | ")}`,
      );
    }

    const conducted = interview.status === "conducted";
    if (conducted) {
      if (!(CENSUS_INTERVIEW_EVIDENCE_CLASSES as readonly string[]).includes(String(interview.evidence_class))) {
        errors.push(
          "metadata.interview.evidence_class is required when status is conducted, and must be " +
            `${CENSUS_INTERVIEW_EVIDENCE_CLASSES.join(" | ")} — Decision 22: the deciding fact is who is ` +
            "asking, so the evidence class cannot be omitted",
        );
      }
      const who = interview.interviewer;
      if (!isPlainObject(who)) {
        errors.push("metadata.interview.interviewer is required when status is conducted");
      } else {
        if (!isNonEmptyString(who.role)) errors.push("metadata.interview.interviewer.role is required");
        if (typeof who.independent_of_executor !== "boolean") {
          errors.push(
            "metadata.interview.interviewer.independent_of_executor must be a boolean; it is " +
              "never inferred, because Decision 22's no-self-review rule must be assertable",
          );
        }
        // Decision 22's load-bearing rule, made checkable: an independent interrogation
        // cannot be conducted by the agent whose run it examines. This is the one place a
        // schema can enforce "no self-review" without adjudicating anything.
        if (
          isNonEmptyString(who.role)
          && who.independent_of_executor === true
          && isPlainObject(metadata.census)
          && isNonEmptyString(metadata.census.executed_by_role)
          && String(who.role) === String(metadata.census.executed_by_role)
        ) {
          errors.push(
            `metadata.interview.interviewer.role "${who.role}" is the executing agent's own role ` +
              "(census.executed_by_role), so it cannot be marked independent_of_executor — " +
              "Decision 22 forbids adjudicating your own participation in the interview",
          );
        }
        if (
          isNonEmptyString(who.role)
          && interview.evidence_class === "self_report"
          && isPlainObject(metadata.census)
          && isNonEmptyString(metadata.census.executed_by_role)
          && String(who.role) !== String(metadata.census.executed_by_role)
        ) {
          errors.push(
            "metadata.interview.evidence_class is self_report but the interviewer role does not " +
              `match census.executed_by_role ("${metadata.census.executed_by_role}"); a ` +
              "self-report must be the executing agent",
          );
        }
      }
    } else {
      if (interview.evidence_class !== undefined) {
        errors.push("metadata.interview.evidence_class is only meaningful when status is conducted");
      }
      if (interview.interviewer !== undefined) {
        errors.push("metadata.interview.interviewer is only meaningful when status is conducted");
      }
      if (interview.status === "declined" && !isNonEmptyString(interview.decline_reason)) {
        errors.push(
          "metadata.interview.decline_reason is required when declined: Decision 22 treats a " +
            "refusal as an exercise of agency that is weighed, so it must be recorded, not inferred",
        );
      }
    }
  }

  const recommended = metadata.recommended_updates;
  if (!Array.isArray(recommended)) {
    errors.push("metadata.recommended_updates must be an array");
  } else {
    const allowed: readonly string[] = ["new-card", "amend-card", "retire-card", "no-action"];
    recommended.forEach((update, index) => {
      const at = `metadata.recommended_updates[${index}]`;
      if (!isPlainObject(update)) {
        errors.push(`${at} must be an object`);
        return;
      }
      if (!allowed.includes(String(update.kind))) errors.push(`${at}.kind is not a known update kind`);
      if (update.target_asset_id !== null && !isNonEmptyString(update.target_asset_id)) {
        errors.push(`${at}.target_asset_id must be a string or null`);
      }
      if (!isNonEmptyString(update.rationale)) errors.push(`${at}.rationale is required`);
      if (!Array.isArray(update.supporting_census_ids)
        || !update.supporting_census_ids.every((id: unknown) => isNonEmptyString(id))) {
        errors.push(`${at}.supporting_census_ids must be an array of census ids`);
      }
    });
  }

  return { ok: errors.length === 0, errors };
}

export interface CensusStoredReport {
  id: string;
  created_at: string;
  metadata: CensusReportMetadata;
}

export interface CensusCardIndexEntry {
  asset_id: string;
  report_count: number;
  finding_count: number;
  category_counts: Partial<Record<CensusCategory, number>>;
  anticipated_finding_count: number;
}

export interface CensusDailyCount {
  /** UTC calendar day, `YYYY-MM-DD`. */
  day: string;
  report_count: number;
  finding_count: number;
  /** Reports for that day that carried no finding — a complete record of nothing observed. */
  report_count_no_findings: number;
}

export interface CensusReportIndex {
  generated_at: string;
  read_only: true;
  basis: string;
  limitation: string;
  report_count: number;
  report_ids_with_no_findings: number;
  doctrine_snapshot_count: number;
  trigger_counts: Partial<Record<CensusTrigger, number>>;
  category_counts: Partial<Record<CensusCategory, number>>;
  cards: CensusCardIndexEntry[];
  truncated: boolean;
  /**
   * Q6 retention instrumentation. The Architect accepted unbounded growth "for now, with
   * measurement required from day one", on the reasoning that rows/day extrapolates
   * directly and is better sized against a measurement than a guess. This is that
   * measurement — it is why `limit`-bounded reads are still useful for planning.
   *
   * Bounded by the rows the caller read, so it is a floor on true daily volume, not a
   * census of it. `truncated` tells you when that floor is not the whole truth.
   */
  daily_counts: CensusDailyCount[];
  /** Days observed in `daily_counts`. Extrapolating rows/day needs this as the denominator. */
  observed_day_count: number;
}

/**
 * Build the derived read model over canonical census rows: per-card observation counts and
 * category totals. Pure aggregation, no policy.
 *
 * Governance thresholds (the 30% sampled-missing signal, the 3-consecutive-same-gap
 * auto-create) are deliberately NOT here — they are A5, and putting them here would give
 * the read path a second opinion about what the numbers mean.
 */
export function buildCensusReportIndex(
  reports: CensusStoredReport[],
  options: { truncated?: boolean } = {},
): CensusReportIndex {
  const cards = new Map<string, CensusCardIndexEntry>();
  const categoryCounts: Partial<Record<CensusCategory, number>> = {};
  const triggerCounts: Partial<Record<CensusTrigger, number>> = {};
  const doctrineSnapshots = new Set<string>();
  let emptyReports = 0;

  for (const report of reports) {
    const snapshotId = report.metadata?.frame?.doctrine_snapshot_id;
    if (isNonEmptyString(snapshotId)) doctrineSnapshots.add(snapshotId);

    for (const trigger of report.metadata?.census?.triggers || []) {
      triggerCounts[trigger] = (triggerCounts[trigger] || 0) + 1;
    }

    const findings = report.metadata?.findings || [];
    if (findings.length === 0) emptyReports += 1;

    for (const finding of findings) {
      const assetId = finding?.card?.asset_id;
      if (!isNonEmptyString(assetId)) continue;
      const category = finding.category;
      categoryCounts[category] = (categoryCounts[category] || 0) + 1;
      const entry = cards.get(assetId) || {
        asset_id: assetId,
        report_count: 0,
        finding_count: 0,
        category_counts: {},
        anticipated_finding_count: 0,
      };
      entry.finding_count += 1;
      entry.category_counts[category] = (entry.category_counts[category] || 0) + 1;
      if (finding.anticipated === false) entry.anticipated_finding_count += 1;
      cards.set(assetId, entry);
    }
  }

  // report_count per card: a report that produced no finding still observed that card, so
  // count it from the frame's card set when present, otherwise from findings alone.
  const reportCounts = new Map<string, number>();
  for (const report of reports) {
    const assets = new Set<string>();
    for (const finding of report.metadata?.findings || []) {
      if (isNonEmptyString(finding?.card?.asset_id)) assets.add(finding.card.asset_id);
    }
    for (const assetId of assets) reportCounts.set(assetId, (reportCounts.get(assetId) || 0) + 1);
  }
  for (const [assetId, entry] of cards) {
    entry.report_count = reportCounts.get(assetId) || 0;
  }

  const orderedCards = [...cards.values()].sort(
    (left, right) => right.finding_count - left.finding_count || left.asset_id.localeCompare(right.asset_id),
  );

  // Q6 retention instrumentation: rows per UTC calendar day, so A6 can size retention
  // against a measurement instead of a guess.
  const dailyBuckets = new Map<string, CensusDailyCount>();
  for (const report of reports) {
    const day = String(report.created_at || "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) continue;
    const bucket = dailyBuckets.get(day) || {
      day,
      report_count: 0,
      finding_count: 0,
      report_count_no_findings: 0,
    };
    bucket.report_count += 1;
    const findings = report.metadata?.findings || [];
    bucket.finding_count += findings.length;
    if (findings.length === 0) bucket.report_count_no_findings += 1;
    dailyBuckets.set(day, bucket);
  }
  const dailyCounts = [...dailyBuckets.values()].sort((left, right) => left.day.localeCompare(right.day));

  return {
    generated_at: new Date().toISOString(),
    read_only: true,
    basis:
      "Derived on demand from canonical nebula.agent_records rows tagged `census`. Counts are " +
      "observations of what a census reported, not verified activations and not causal influence.",
    limitation:
      "No governance threshold is applied here; threshold policy is A5. A card absent from this " +
      "index was not reported in a finding — which is not the same as being correctly present, " +
      "because a sampled execution with zero findings is a complete record of nothing observed. " +
      "daily_counts is bounded by the rows this call read, so it is a floor on true daily volume " +
      "whenever truncated is true.",
    report_count: reports.length,
    report_ids_with_no_findings: emptyReports,
    doctrine_snapshot_count: doctrineSnapshots.size,
    trigger_counts: triggerCounts,
    category_counts: categoryCounts,
    cards: orderedCards,
    truncated: Boolean(options.truncated),
    daily_counts: dailyCounts,
    observed_day_count: dailyCounts.length,
  };
}
