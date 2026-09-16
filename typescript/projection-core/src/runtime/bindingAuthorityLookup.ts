import type { PgQueryable } from './doctrineLookup.pg.js';

/**
 * G1 activation — binding authority consult (read-only, fail-safe).
 *
 * Resolves, for one decision class + subject, whether the admission
 * boundary must enforce a persisted PEB denial. Backed by two reads
 * against the nexus DB (server-owned `queryable`, no credentials here):
 *
 *   1. peb.state row `binding_authority_mode` (migration V135) — the
 *      durable authority mode; only `deny_contract_promotion` may carry
 *      `narrowly_binding`.
 *   2. peb.binding_decision_evidence — the latest PEB disposition for the
 *      subject (V133 evidence table; read-only).
 *
 * Fail-safe contract (mirrors peb-kernel binding_authority.py):
 *   - any query error / malformed row -> `advisory` (never widen authority)
 *   - missing state row               -> `advisory` (pre-activation ==
 *                                        post-reversion behavior)
 *   - a class other than the binding class -> `advisory`
 *   - never throws; never writes.
 *
 * Blocking semantics at the boundary (W1.10 grant 05d0fe54 + verdict
 * 986ec482): narrowly blocking means the boundary refuses ONLY subjects
 * with a persisted NEGATIVE PEB disposition. Absence of a decision is not
 * a denial (no broad peb.decisions activation); `allow` or absent
 * dispositions proceed structurally.
 */

export const BINDING_DECISION_CLASS = "deny_contract_promotion";
export const AUTHORITY_STATE_KEY = "binding_authority_mode";
export const ADVISORY = "advisory" as const;
export const NARROWLY_BINDING = "narrowly_binding" as const;

/** Dispositions that DENY at the boundary when authority is binding. */
export const NEGATIVE_DISPOSITIONS: ReadonlySet<string> = new Set([
  "refused", "unknown", "stale", "drift", "quarantined", "superseded", "rolled_back",
]);

export interface BindingConsultResult {
  authority_level: typeof ADVISORY | typeof NARROWLY_BINDING;
  /** Latest persisted PEB disposition for the subject; null when none. */
  latest_disposition: string | null;
  /** True when latest_disposition is a negative (denying) disposition. */
  denying: boolean;
  decision_class: string;
  subject_id: string;
  state_version: number | null;
  reason: string;
}

export class PgBindingAuthorityConsult {
  private readonly queryable: PgQueryable;
  private readonly ttlMs: number;
  private cache: {
    authority: typeof ADVISORY | typeof NARROWLY_BINDING;
    stateVersion: number | null;
    at: number;
  } | null = null;

  constructor(queryable: PgQueryable, ttlMs = 30_000) {
    this.queryable = queryable;
    this.ttlMs = ttlMs;
  }

  /** Drop the authority cache (e.g. after a reversion operation). */
  invalidate(): void {
    this.cache = null;
  }

  async consult(decisionClass: string, subjectId: string): Promise<BindingConsultResult> {
    const base = {
      decision_class: decisionClass,
      subject_id: subjectId,
    };
    // 1. Authority mode (cached briefly; a reversion propagates within TTL).
    let authority: typeof ADVISORY | typeof NARROWLY_BINDING = ADVISORY;
    let stateVersion: number | null = null;
    let reason = "advisory_mode";
    try {
      const now = Date.now();
      if (this.cache && now - this.cache.at < this.ttlMs) {
        authority = this.cache.authority;
        stateVersion = this.cache.stateVersion;
        reason = authority === NARROWLY_BINDING ? "state_row" : "advisory_mode";
      } else {
        const { rows } = await this.queryable.query(
          `SELECT content, version FROM peb.state WHERE key = $1`,
          [AUTHORITY_STATE_KEY],
        );
        if (rows.length === 0) {
          authority = ADVISORY;
          stateVersion = null;
          reason = "no_state_row";
        } else {
          const content = rows[0].content ?? {};
          stateVersion = typeof rows[0].version === "number" ? rows[0].version : null;
          if (
            content.decision_class === BINDING_DECISION_CLASS &&
            content.authority_level === NARROWLY_BINDING &&
            decisionClass === BINDING_DECISION_CLASS
          ) {
            authority = NARROWLY_BINDING;
            reason = "state_row";
          } else {
            authority = ADVISORY;
            reason = content.decision_class === decisionClass ? "advisory_mode" : "class_not_elevated";
          }
        }
        this.cache = { authority, stateVersion, at: now };
      }
    } catch (err) {
      authority = ADVISORY;
      stateVersion = null;
      reason = `state_lookup_error:${err instanceof Error ? err.message : String(err)}`;
    }

    if (authority !== NARROWLY_BINDING) {
      return { ...base, authority_level: ADVISORY, latest_disposition: null, denying: false, state_version: stateVersion, reason };
    }

    // 2. Latest persisted disposition for the subject (binding mode only).
    try {
      const { rows } = await this.queryable.query(
        `SELECT disposition FROM peb.binding_decision_evidence
          WHERE decision_class = $1 AND subject_id = $2
          ORDER BY created_at DESC LIMIT 1`,
        [BINDING_DECISION_CLASS, subjectId],
      );
      const disposition = rows.length > 0 ? String(rows[0].disposition) : null;
      const denying = disposition !== null && NEGATIVE_DISPOSITIONS.has(disposition);
      return {
        ...base,
        authority_level: NARROWLY_BINDING,
        latest_disposition: disposition,
        denying,
        state_version: stateVersion,
        reason: disposition === null ? "no_decision_for_subject" : "latest_evidence_row",
      };
    } catch (err) {
      // Cannot resolve the disposition — fail-safe: advisory for this call
      // (never deny on a broken consult; never block unevaluated subjects).
      return {
        ...base,
        authority_level: ADVISORY,
        latest_disposition: null,
        denying: false,
        state_version: stateVersion,
        reason: `disposition_lookup_error:${err instanceof Error ? err.message : String(err)}`,
      };
    }
  }
}
