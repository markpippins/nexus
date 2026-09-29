/**
 * Execution identity and the Tier-2 census marker — CDLC A2a.
 *
 * Authority:
 *   - `dffa404e` — DBA ruling. The operative unblock; four conditions, each encoded below.
 *   - `6629b009` — three-tier split by lifecycle. Tier 1 is the execution record, Tier 2 is
 *     the sampling marker carried on it, Tier 3 is the census report.
 *   - `a27dc653` — naming. The marker is `census: { sampled, census_id, rate }`.
 *   - `c3d1c0a8` + `lib/census-report.ts` — the Tier-3 shape this envelope must join to.
 *
 * ## What this module is, and is not
 *
 * It defines the **identity and the marker**, plus the validator and the cross-tier join
 * contract. It does **not** bind them to a store. The Architect wrote "Execution record
 * (new)" without naming one, and this module deliberately does not invent a table — that
 * is a migration, and the DBA ruling authorises *schema design only*:
 *
 *   "This ruling authorizes schema design for A2a only. It does not authorize any migration
 *    to be applied."
 *
 * ## Strict where a ruling exists, permissive where none does
 *
 * Validation is strict on everything a decision actually fixed: the four DBA conditions, the
 * tri-state marker, absence-illegal, and the `census_id ⟺ sampled` join. It is deliberately
 * permissive on `outcome.status` and the subject identifiers, because **no vocabulary has
 * been ratified for either**, and inventing one here would be a second source of truth for
 * a concept the Architect has not decided.
 *
 * ## No dependency on the census-report module, deliberately
 *
 * `assertCensusJoin` is **structural** — it checks the relationship between tiers, not
 * whether a Tier-3 payload satisfies its own contract. Each tier validates its own shape:
 * this module owns Tier 1/Tier 2, and the census read path already validates Tier-3 rows on
 * read and quarantines failures into `rejected_reports`. Importing the other validator here
 * would make A2a depend on A2b, put two sources of truth in one place, and buy nothing.
 *
 * ## The four DBA conditions
 *
 * 1. Distinct `execution_id uuid`, independently minted. Never reuse or normalize
 *    `session_id`.
 * 2. Session is a **reference, not a parent**: `text`/nullable, many executions per session.
 *    No FK, no join, no parent lookup is modelled.
 * 3. A `vision.sessions` reference must be a **separate named column**; `session_id` must
 *    never carry both meanings. Condition 3 is enforced mechanically here — a uuid-shaped
 *    `session_id` is **rejected**, because that is precisely the two-concept collision V184
 *    already commits (`vision.sessions.session_id uuid` vs the `text` reference family).
 * 4. **No FK to `vision.sessions`** — the table is empty and its one FK is 0/15,253
 *    populated. No FK is declared.
 *
 * ## Tier-2 rules
 *
 * Explicit tri-state, **absence is illegal**, and never a durable record-store row. The
 * three states are: census not enabled / enabled but not sampled / enabled and sampled. The
 * marker object must always be present, because A5's threshold is "30% sampled-missing",
 * which is only computable if a missing marker can never be confused with a disabled census.
 */

export interface CensusTier3Ref {
  census_id: string;
  execution_id: string;
  sampled: boolean;
}

export const EXECUTION_SCHEMA_VERSION = 1;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const RATE_RE = /^(\d+)\/(\d+)$/;

/** The named-session convention the DBA observed live, e.g. `reviewer-20260703-112402-831cbac9`. */
const SESSION_ID_EXAMPLE = "reviewer-20260703-112402-831cbac9";

/**
 * The three states of the Tier-2 marker. Absence of the marker is a fourth state and is
 * illegal — see the module header.
 */
export type CensusMarkerState = "not-enabled" | "enabled-not-sampled" | "enabled-sampled";

export interface CensusMarker {
  /** Was `--census` passed at all? */
  enabled: boolean;
  /** Did this execution get selected? Implies `enabled`. */
  sampled: boolean;
  /** Effective base rate as `N/M`; null means master-switch-only, which is 1/1. */
  rate: string | null;
}

export interface ExecutionIdentity {
  schema_version: typeof EXECUTION_SCHEMA_VERSION;
  /** Independently minted. Never derived from, or normalized against, `session_id`. */
  execution_id: string;
  /**
   * Opaque text reference to a harness session, or null when the execution is un-shimmed.
   *
   * A **reference, not a parent**: many executions per session. Carried through opaquely —
   * this module never parses it. Rejected if uuid-shaped, because a uuid in this field is
   * the `vision.sessions` concept colliding with the reference family (DBA condition 3).
   */
  session_id: string | null;
  /** Non-null exactly when sampled; the join key to the single Tier-3 census report. */
  census_id: string | null;
}

export interface ExecutionSubject {
  /**
   * What ran, e.g. `conduit`, `wind`. Required; no closed vocabulary has been ratified.
   *
   * Named `source_namespace` rather than `namespace`: `namespace` is a reserved TypeSpec
   * keyword and breaks the contract parser, and `source_namespace` is the existing house
   * convention (doctrine transition records, and the census read endpoint's query param).
   */
  source_namespace: string;
  ticket_id?: string | null;
  work_item_id?: string | null;
}

export interface ExecutionFrame {
  /** B1/B3 provenance: the doctrine in force, content-addressed. */
  doctrine_snapshot_id: string;
  procedure_card_set_hash?: string;
}

export interface ExecutionOutcome {
  /**
   * Free text, required. Deliberately unconstrained: no outcome vocabulary has been
   * ratified, and `6629b009` scopes A2a to identity, so enumerating outcomes here would
   * be this module inventing policy.
   */
  status: string;
  detail?: string | null;
}

export interface ExecutionEnvelope {
  execution: ExecutionIdentity;
  /** Tier-2 marker. Always present — absence is illegal. Never a durable record-store row. */
  census: CensusMarker;
  subject: ExecutionSubject;
  frame: ExecutionFrame;
  outcome: ExecutionOutcome;
}

export interface ExecutionValidationResult {
  ok: boolean;
  errors: string[];
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** Naming guard (`a27dc653`): no B-series checkpoint vocabulary in an execution payload. */
function collectDeltaKeys(value: unknown, path: string, out: string[]): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectDeltaKeys(item, `${path}[${index}]`, out));
    return;
  }
  if (!isPlainObject(value)) return;
  for (const [key, child] of Object.entries(value)) {
    const childPath = path ? `${path}.${key}` : key;
    if (key === "delta" || key === "delta_steps" || key === "base_version" || key === "prevVersion") {
      out.push(`${childPath} is a B-series checkpoint key and is forbidden in an execution payload`);
    }
    collectDeltaKeys(child, childPath, out);
  }
}

/** Classify the marker, or return null when the marker is not internally consistent. */
export function censusMarkerState(marker: CensusMarker): CensusMarkerState | null {
  if (!marker || typeof marker !== "object") return null;
  if (typeof marker.enabled !== "boolean" || typeof marker.sampled !== "boolean") return null;
  if (marker.sampled && !marker.enabled) return null;
  if (!marker.enabled && (marker.rate !== null || marker.sampled)) return null;
  return marker.sampled ? "enabled-sampled" : marker.enabled ? "enabled-not-sampled" : "not-enabled";
}

/**
 * Validate an execution envelope.
 *
 * Strict on ratified decisions, permissive on unratified vocabulary. Collects every error
 * rather than throwing on the first, so a caller can report a complete rejection in one pass.
 */
export function validateExecutionEnvelope(envelope: unknown): ExecutionValidationResult {
  const errors: string[] = [];
  collectDeltaKeys(envelope, "envelope", errors);

  if (!isPlainObject(envelope)) {
    return { ok: false, errors: [...errors, "envelope must be an object"] };
  }

  const execution = envelope.execution;
  if (!isPlainObject(execution)) {
    errors.push("envelope.execution is required and must be an object");
  } else {
    if (execution.schema_version !== EXECUTION_SCHEMA_VERSION) {
      errors.push(`envelope.execution.schema_version must be ${EXECUTION_SCHEMA_VERSION}`);
    }
    // DBA condition 1: distinct, independently minted, uuid.
    if (!isNonEmptyString(execution.execution_id)) {
      errors.push("envelope.execution.execution_id is required");
    } else if (!UUID_RE.test(execution.execution_id)) {
      errors.push(
        `envelope.execution.execution_id must be a uuid; got "${execution.execution_id}". ` +
          "It is minted independently and never derived from session_id (DBA condition 1).",
      );
    }

    // DBA conditions 2 and 3: opaque text reference, never a uuid.
    if (execution.session_id !== null) {
      if (!isNonEmptyString(execution.session_id)) {
        errors.push("envelope.execution.session_id must be a non-empty string or null");
      } else if (UUID_RE.test(execution.session_id)) {
        errors.push(
          "envelope.execution.session_id must not be uuid-shaped. A uuid here is the " +
            "vision.sessions concept colliding with the session reference family; if a " +
            "calendar session is wanted it needs its own separately named column and FK " +
            "(DBA condition 3).",
        );
      }
    }

    // census_id is the join key to the single Tier-3 row; shape is uuid, presence is checked
    // against the marker below.
    if (execution.census_id !== null) {
      if (!isNonEmptyString(execution.census_id)) {
        errors.push("envelope.execution.census_id must be a non-empty string or null");
      } else if (!UUID_RE.test(execution.census_id)) {
        errors.push("envelope.execution.census_id must be a uuid when present");
      }
    }
  }

  // Tier-2: absence of the marker is illegal.
  if (!isPlainObject(envelope.census)) {
    errors.push(
      "envelope.census is required and must be an object; absence is illegal because a missing " +
        "marker must never be confusable with a disabled census (A5 measures 30% sampled-missing)",
    );
  } else {
    const marker = envelope.census as Record<string, unknown>;
    const state = censusMarkerState(marker as unknown as CensusMarker);
    if (state === null) {
      errors.push(
        "envelope.census is not a valid tri-state: sampled implies enabled, and a disabled " +
          "census must carry sampled=false with rate=null",
      );
    }
    if (marker.rate !== null) {
      if (!isNonEmptyString(marker.rate)) {
        errors.push("envelope.census.rate must be an \"N/M\" string or null");
      } else {
        const match = RATE_RE.exec(marker.rate);
        if (!match) {
          errors.push(`envelope.census.rate must match N/M; got ${marker.rate}`);
        } else {
          const numerator = Number(match[1]);
          const denominator = Number(match[2]);
          if (denominator < 1) errors.push("envelope.census.rate denominator must be >= 1");
          if (numerator > denominator) errors.push("envelope.census.rate must satisfy N <= M");
        }
      }
    }
    // The join invariant's Tier-1/Tier-2 half.
    const censusId = isPlainObject(execution) ? execution.census_id : null;
    if (state === "enabled-sampled" && censusId == null) {
      errors.push("envelope.execution.census_id is required when the marker says sampled");
    }
    if (state !== null && state !== "enabled-sampled" && censusId != null) {
      errors.push("envelope.execution.census_id must be null unless the marker says sampled");
    }
  }

  const subject = envelope.subject;
  if (!isPlainObject(subject)) {
    errors.push("envelope.subject is required and must be an object (what ran)");
  } else if (!isNonEmptyString(subject.source_namespace)) {
    errors.push("envelope.subject.source_namespace is required (e.g. \"conduit\", \"wind\")");
  }

  const frame = envelope.frame;
  if (!isPlainObject(frame)) {
    errors.push("envelope.frame is required and must be an object");
  } else {
    if (!isNonEmptyString(frame.doctrine_snapshot_id)) {
      errors.push("envelope.frame.doctrine_snapshot_id is required (the doctrine in force, per B1/B3)");
    }
    if ("procedure_card_set_hash" in frame && frame.procedure_card_set_hash !== null
      && !isNonEmptyString(frame.procedure_card_set_hash)) {
      errors.push("envelope.frame.procedure_card_set_hash must be a string when present");
    }
  }

  const outcome = envelope.outcome;
  if (!isPlainObject(outcome)) {
    errors.push("envelope.outcome is required and must be an object");
  } else if (!isNonEmptyString(outcome.status)) {
    errors.push("envelope.outcome.status is required (free text; no vocabulary is ratified)");
  }

  return { ok: errors.length === 0, errors };
}

export interface CensusJoinResult {
  ok: boolean;
  errors: string[];
}

/**
 * The cross-tier join invariant from `6629b009`, made executable.
 *
 *   `census_id != null`  ⟺  `sampled`  ⟺  **exactly one** Tier-3 census report
 *
 * Unsampled ⟹ **no** Tier-3 row. The third leg is what A5's "observed nothing" denominator
 * depends on: a sampled execution with zero findings still owes a row, or "not observed"
 * becomes indistinguishable from "observed nothing".
 *
 * Takes Tier-3 rows as structural references, not full payloads. Each tier validates its own
 * shape — see the header note on why this does not import the census-report validator.
 */
export function assertCensusJoin(
  envelope: ExecutionEnvelope,
  reports: CensusTier3Ref[],
): CensusJoinResult {
  const errors: string[] = [];
  const envelopeValidation = validateExecutionEnvelope(envelope);
  if (!envelopeValidation.ok) return envelopeValidation;

  const state = censusMarkerState(envelope.census);
  const censusId = envelope.execution.census_id;
  const executionId = envelope.execution.execution_id;

  if (state === "enabled-sampled") {
    // Sampled: exactly one row, keyed by census_id.
    const matching = censusId ? reports.filter((report) => report?.census_id === censusId) : [];
    if (matching.length !== 1) {
      errors.push(
        `sampled execution ${executionId} must have exactly one census report; found ${matching.length}`,
      );
    }
    for (const report of matching) {
      if (report.execution_id !== executionId) {
        errors.push(
          `census report ${report.census_id} names execution_id ` +
            `${report.execution_id}, which does not match this execution`,
        );
      }
    }
  } else {
    // Unsampled: no report may reference this execution at all. Keyed on execution_id
    // rather than census_id — an unsampled execution has census_id null, so filtering by
    // it here would silently pass on a stray row, which is exactly the case worth catching.
    const byExecution = reports.filter((report) => report?.execution_id === executionId);
    if (byExecution.length > 0) {
      errors.push(
        `unsampled execution ${executionId} must have no census report; found ${byExecution.length}`,
      );
    }
  }

  return { ok: errors.length === 0, errors };
}

export const EXECUTION_IDENTITY_NOTES = {
  session_id_example: SESSION_ID_EXAMPLE,
  conditions:
    "1 distinct execution_id uuid; 2 session_id is a text reference, many-to-one, no FK; " +
    "3 a calendar session needs its own named column, never an overloaded session_id; " +
    "4 no FK to vision.sessions (empty table, its one FK is 0/15253 populated).",
  store:
    "No store is bound by this module. The execution record's home is unresolved in " +
    "6629b009 and the DBA ruling authorises schema design only, not a migration.",
  migration:
    "The execution_id uuid column requires the owning migration source plus an R9 " +
    "vanadium-replication answer before it can be applied.",
};
