/**
 * Census post-action interview — OUTCOME record (CDLC A2b, Architect Decision 22).
 *
 * ## Why this is a separate record and not a wider census field
 *
 * `c3d1c0a8` Q4 is explicit: the interview outcome *"lands as a follow-on record referencing
 * `census_id` — append-only, no rewrite of the census row"* and *"please do not retrofit the
 * outcome into the existing report shape."*
 *
 * A2b (PR #634) therefore validates the census report with `interview: { status }` only, and
 * refuses any other key. **I got this wrong first time**: I widened the census report's
 * `interview` object to carry `evidenceClass`/`interviewer` in place, which is precisely the
 * retrofit that was ruled out. The contract-casing ratchet caught it as a side effect (the new
 * fields were snake_case with no column and no computation, so there was no honest exemption),
 * but the ratchet was not the point — the ruling was, and I should have read it before
 * implementing.
 *
 * So the outcome is its own record. That also makes append-only real: the census row is written
 * once at sampled-execution time and is never edited again, so an interview conducted days later
 * cannot alter what the census originally reported.
 *
 * ## What Decision 22 made structural
 *
 * *"A protocol dispute over an interview is ruled by the architect. A review dispute over the
 * findings is ruled by the reviewer. Neither may adjudicate their own participation in the
 * interview."* And: the deciding fact in the post-action path is not the wording of a question
 * but **who is asking**.
 *
 * Both are enforced below rather than documented:
 *   - `evidenceClass` is required whenever an interview was conducted, so a record cannot say
 *     only "interviewed" and leave the reader to guess whether the answer carried weight.
 *   - `independentOfExecutor` is an explicit boolean, never inferred.
 *   - An `independent_interrogation` whose interviewer is the executing agent is REJECTED, and a
 *     `self_report` whose interviewer is not the executing agent is REJECTED. This is the one
 *     place a schema can make "no self-review" checkable without adjudicating anything: the
 *     record simply cannot assert a contradiction about its own provenance.
 *   - `declined` is a first-class outcome with a required `declineReason`. A refusal is an
 *     exercise of the executing agent's agency, weighed rather than treated as self-refuting,
 *     so it is recorded rather than inferred from an absence.
 *
 * ## Why these fields are camelCase
 *
 * This is a consumer-visible record read by a reviewer or architect, and it names no database
 * column — which is exactly the case the casing doctrine (`19d6f725`) says should be camelCase.
 * The census report's snake_case fields are exempt because they mirror columns or a named
 * computation; these do neither.
 *
 * ## What this module deliberately does not settle
 *
 * The **v1 scope** (context-only vs reasoning/process) is parked with the Architect
 * (Decision 22: *"parked for A2b implementation to propose"*). It is a question about method,
 * not shape, so it is absent here. This module validates structure and settles nothing.
 */

/** Required whenever an interview was conducted. */
export const CENSUS_INTERVIEW_EVIDENCE_CLASSES = [
  "self_report",
  "independent_interrogation",
] as const;

export type CensusInterviewEvidenceClass =
  (typeof CENSUS_INTERVIEW_EVIDENCE_CLASSES)[number];

export const CENSUS_INTERVIEW_STATUSES = ["pending", "conducted", "declined"] as const;

export type CensusInterviewStatus = (typeof CENSUS_INTERVIEW_STATUSES)[number];

/** Schema version for the follow-on record, separate from the census report's. */
export const CENSUS_INTERVIEW_SCHEMA_VERSION = 1;

export interface CensusInterviewOutcome {
  /** camelCase: this record names no column. The census report's own schema_version is a
   *  stored key and stays snake_case under its exemption. */
  schemaVersion: typeof CENSUS_INTERVIEW_SCHEMA_VERSION;
  /** The census this outcome attaches to. The join key; never null. */
  census_id: string;
  /** The execution that was interviewed. Cross-checked against the census report. */
  execution_id: string;
  status: CensusInterviewStatus;
  /** Required when conducted. Absent otherwise. */
  evidenceClass?: CensusInterviewEvidenceClass;
  /** Required when conducted. Absent otherwise. */
  interviewer?: CensusInterviewer;
  /** Required when declined. Never interpreted by this module. */
  declineReason?: string;
}

export interface CensusInterviewer {
  role: string;
  /** Renamed from `model`: `model` is a reserved TypeSpec keyword. */
  modelId?: string | null;
  /**
   * Stated, never inferred. Decision 22's no-self-review rule is only assertable if the record
   * carries the claim explicitly.
   */
  independentOfExecutor: boolean;
}

export interface CensusOutcomeValidationResult {
  ok: boolean;
  errors: string[];
}

const OUTCOME_KEYS = [
  "schemaVersion",
  "census_id",
  "execution_id",
  "status",
  "evidenceClass",
  "interviewer",
  "declineReason",
];

const INTERVIEWER_KEYS = ["role", "modelId", "independentOfExecutor"];

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/**
 * Validate a follow-on interview outcome record.
 *
 * `executor` is the executing agent's identity as carried on the census report
 * (`census.executed_by_role` / `executed_by_model`). Supplying it is what lets the no-self-review
 * rule be checked here; it is passed in by the caller rather than duplicated, so there is one
 * source of truth for who executed.
 */
export function validateCensusInterviewOutcome(
  outcome: unknown,
  executor?: { role: string; model?: string | null },
): CensusOutcomeValidationResult {
  const errors: string[] = [];

  if (!isPlainObject(outcome)) {
    return { ok: false, errors: ["outcome must be an object"] };
  }

  for (const key of Object.keys(outcome)) {
    if (!OUTCOME_KEYS.includes(key)) {
      errors.push(
        `outcome.${key} is not part of the Decision 22 outcome shape (${OUTCOME_KEYS.join(", ")}); ` +
          "the outcome is a follow-on record, not a widened census report field",
      );
    }
  }

  if (outcome.schemaVersion !== CENSUS_INTERVIEW_SCHEMA_VERSION) {
    errors.push(`outcome.schemaVersion must be ${CENSUS_INTERVIEW_SCHEMA_VERSION}`);
  }
  if (!isNonEmptyString(outcome.census_id)) {
    errors.push("outcome.census_id is required; the outcome attaches to exactly one census report");
  }
  if (!isNonEmptyString(outcome.execution_id)) {
    errors.push("outcome.execution_id is required");
  }
  if (!(CENSUS_INTERVIEW_STATUSES as readonly string[]).includes(String(outcome.status))) {
    errors.push(`outcome.status must be one of ${CENSUS_INTERVIEW_STATUSES.join(" | ")}`);
  }

  const conducted = outcome.status === "conducted";
  if (conducted) {
    if (
      !(CENSUS_INTERVIEW_EVIDENCE_CLASSES as readonly string[]).includes(
        String(outcome.evidenceClass),
      )
    ) {
      errors.push(
        "outcome.evidenceClass is required when conducted, and must be " +
          `${CENSUS_INTERVIEW_EVIDENCE_CLASSES.join(" | ")} — Decision 22: the deciding fact is ` +
          "who is asking, so the evidence class cannot be omitted",
      );
    }

    const who = outcome.interviewer;
    if (!isPlainObject(who)) {
      errors.push("outcome.interviewer is required when conducted");
    } else {
      for (const key of Object.keys(who)) {
        if (!INTERVIEWER_KEYS.includes(key)) {
          errors.push(`outcome.interviewer.${key} is not part of the shape (${INTERVIEWER_KEYS.join(", ")})`);
        }
      }
      if (!isNonEmptyString(who.role)) {
        errors.push("outcome.interviewer.role is required");
      }
      if (typeof who.independentOfExecutor !== "boolean") {
        errors.push(
          "outcome.interviewer.independentOfExecutor must be a boolean; it is never inferred, " +
            "because Decision 22's no-self-review rule must be assertable",
        );
      }

      // Decision 22's load-bearing rule, made checkable.
      if (executor && isNonEmptyString(who.role)) {
        const sameAgent =
          String(who.role) === String(executor.role) &&
          (!who.modelId || !executor.model || String(who.modelId) === String(executor.model));
        if (outcome.evidenceClass === "independent_interrogation" && sameAgent) {
          errors.push(
            `outcome.interviewer is the executing agent (${who.role}${who.modelId ? `/${who.modelId}` : ""}), ` +
              "so it cannot be an independent_interrogation — Decision 22 forbids adjudicating " +
              "your own participation in the interview",
          );
        }
        if (outcome.evidenceClass === "self_report" && !sameAgent) {
          errors.push(
            "outcome.evidenceClass is self_report but the interviewer is not the executing agent " +
              `(${executor.role}${executor.model ? `/${executor.model}` : ""}); a self-report must ` +
              "be the executing agent describing its own run",
          );
        }
      }
    }
  } else {
    if (outcome.evidenceClass !== undefined) {
      errors.push("outcome.evidenceClass is only meaningful when status is conducted");
    }
    if (outcome.interviewer !== undefined) {
      errors.push("outcome.interviewer is only meaningful when status is conducted");
    }
    if (outcome.status === "declined" && !isNonEmptyString(outcome.declineReason)) {
      errors.push(
        "outcome.declineReason is required when declined: Decision 22 treats a refusal as an " +
          "exercise of agency that is weighed, so it must be recorded rather than inferred",
      );
    }
  }

  return { ok: errors.length === 0, errors };
}
