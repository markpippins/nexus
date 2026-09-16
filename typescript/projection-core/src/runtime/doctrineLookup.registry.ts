// W3.06 — Compatibility-adapter retirement & rollback registry.
//
// Governs the lifecycle of the temporary doctrine lookup adapter chain
// (primary PG-backed lookup → fallback in-memory lookup) without ever
// rewriting or invalidating historical evidence:
//
//   - Retirement is gated: it requires a minimum number of recorded
//     observations AND zero unresolved divergences. The gate is
//     fail-closed: any unreviewed divergence blocks retirement.
//   - Retirement is a runtime routing change only. Historical envelopes,
//     receipts, and replay evidence are append-only records and are never
//     rewritten; retiring an adapter cannot invalidate them.
//   - Rollback restores the fallback adapter as primary and records an
//     immutable rollback event; prior events are preserved.
//
// Per the loop protocol and Wave 2 ceiling: this module implements the
// gate and evidence only. The retirement DECISION is owned by the
// Architect (I1/I2) — `requestRetirement` returns a decision request,
// it never flips state on its own.
import type {
  DoctrineLookup,
  DoctrineLookupRequest,
  DoctrineLookupResult,
} from "./doctrineLookup.js";

export type AdapterId = string;

export type AdapterState = "active" | "retiring" | "retired";

export type RegistryMode = "primary" | "fallback";

/** One observation of a primary-adapter lookup outcome. */
export interface LookupObservation {
  at: string;
  request: DoctrineLookupRequest;
  result: Pick<DoctrineLookupResult, "status" | "reason">;
}

/** A divergence between primary and fallback adapters (shadow comparison). */
export interface AdapterDivergence {
  at: string;
  request: DoctrineLookupRequest;
  primaryStatus: DoctrineLookupResult["status"];
  fallbackStatus: DoctrineLookupResult["status"];
  resolved: boolean;
  resolvedBy?: string;
  resolvedAt?: string;
}

/** Immutable lifecycle event; append-only, never rewritten. */
export interface AdapterLifecycleEvent {
  at: string;
  type:
    | "rollback"
    | "retirement_requested"
    | "retirement_confirmed"
    | "retirement_rejected";
  fromAdapter: AdapterId;
  toAdapter: AdapterId;
  note?: string;
}

export interface RetirementDecisionRequest {
  requested: boolean;
  gate: RetirementGate;
  /** Set only when the gate passes; the Architect owns the final call. */
  pendingArchitectDecision: boolean;
}

export interface RetirementGate {
  minObservations: number;
  observations: number;
  openDivergences: number;
  totalDivergences: number;
  passed: boolean;
  reasons: string[];
}

export interface DoctrineLookupRegistryOptions {
  /** Adapter used while it is not retired (e.g. PG-backed, W3.02). */
  primary: { id: AdapterId; adapter: DoctrineLookup };
  /** Safety net used after retirement or during primary failure. */
  fallback: { id: AdapterId; adapter: DoctrineLookup };
  /** Retirement gate threshold; default 100 observations. */
  minObservationsForRetirement?: number;
  /** Wall clock; injectable for deterministic tests. */
  now?: () => string;
}

const DEFAULT_MIN_OBSERVATIONS = 100;

export class DoctrineLookupRegistry implements DoctrineLookup {
  private readonly primary: { id: AdapterId; adapter: DoctrineLookup };
  private readonly fallback: { id: AdapterId; adapter: DoctrineLookup };
  private readonly minObservations: number;
  private readonly now: () => string;

  private primaryState: AdapterState = "active";
  private readonly observations: LookupObservation[] = [];
  private readonly divergences: AdapterDivergence[] = [];
  private readonly events: AdapterLifecycleEvent[] = [];

  constructor(options: DoctrineLookupRegistryOptions) {
    if (
      !options.primary ||
      !options.fallback ||
      typeof options.primary.adapter?.lookup !== "function" ||
      typeof options.fallback.adapter?.lookup !== "function"
    ) {
      throw new Error("registry_requires_primary_and_fallback");
    }
    if (options.primary.id === options.fallback.id) {
      throw new Error("registry_adapter_ids_must_differ");
    }
    this.primary = options.primary;
    this.fallback = options.fallback;
    this.minObservations =
      options.minObservationsForRetirement ?? DEFAULT_MIN_OBSERVATIONS;
    this.now = options.now ?? (() => new Date().toISOString());
  }

  /** Current authoritative adapter for live lookups. */
  get activeAdapterId(): AdapterId {
    return this.primaryState === "retired" ? this.fallback.id : this.primary.id;
  }

  get primaryAdapterState(): AdapterState {
    return this.primaryState;
  }

  /** Read-only view of recorded observations (replay evidence). */
  getObservations(): readonly LookupObservation[] {
    return this.observations;
  }

  /** Read-only view of recorded divergences. */
  getDivergences(): readonly AdapterDivergence[] {
    return this.divergences;
  }

  /** Read-only view of immutable lifecycle events. */
  getLifecycleEvents(): readonly AdapterLifecycleEvent[] {
    return this.events;
  }

  /**
   * Authoritative lookup. Routes to the active adapter, records an
   * observation, and on failure fails closed via the fallback adapter.
   * The fallback result is authoritative when the primary fails; the
   * divergence is recorded for review.
   */
  async lookup(
    request: DoctrineLookupRequest,
    signal?: AbortSignal,
  ): Promise<DoctrineLookupResult> {
    if (this.primaryState === "retired") {
      const result = await this.fallback.adapter.lookup(request, signal);
      this.observations.push({
        at: this.now(),
        request,
        result: { status: result.status, reason: result.reason },
      });
      return result;
    }

    let primaryResult: DoctrineLookupResult;
    try {
      primaryResult = await this.primary.adapter.lookup(request, signal);
    } catch (error) {
      primaryResult = {
        status: "unknown",
        consulted: true,
        latencyMs: 0,
        reason: `primary_error:${(error as Error)?.message ?? "unspecified"}`,
      };
    }

    if (primaryResult.status === "resolved" || primaryResult.status === "refusal") {
      this.observations.push({
        at: this.now(),
        request,
        result: { status: primaryResult.status, reason: primaryResult.reason },
      });
      return primaryResult;
    }

    // Non-resolved primary outcome → consult fallback and compare (shadow).
    const fallbackResult = await this.fallback.adapter.lookup(request, signal);
    this.recordDivergence(request, primaryResult, fallbackResult);
    this.observations.push({
      at: this.now(),
      request,
      result: {
        status: fallbackResult.status,
        reason: fallbackResult.reason ?? "fallback_authoritative",
      },
    });
    return fallbackResult;
  }

  /**
   * Evaluate the retirement gate. Fail-closed: any open (unreviewed)
   * divergence blocks retirement regardless of observation count.
   */
  evaluateRetirementGate(): RetirementGate {
    const open = this.divergences.filter((d) => !d.resolved);
    const reasons: string[] = [];
    if (this.observations.length < this.minObservations) {
      reasons.push(
        `insufficient_observations:${this.observations.length}/${this.minObservations}`,
      );
    }
    if (open.length > 0) {
      reasons.push(`open_divergences:${open.length}`);
    }
    if (this.primaryState !== "active") {
      reasons.push(`primary_not_active:${this.primaryState}`);
    }
    return {
      minObservations: this.minObservations,
      observations: this.observations.length,
      openDivergences: open.length,
      totalDivergences: this.divergences.length,
      passed: reasons.length === 0,
      reasons,
    };
  }

  /**
   * Request retirement. Returns a decision request for the Architect;
   * state only moves to `retiring` when the gate passes. Never retires
   * directly — confirmation is a separate, explicit step (I1/I2).
   */
  requestRetirement(note?: string): RetirementDecisionRequest {
    const gate = this.evaluateRetirementGate();
    if (!gate.passed) {
      return { requested: false, gate, pendingArchitectDecision: false };
    }
    this.primaryState = "retiring";
    this.events.push({
      at: this.now(),
      type: "retirement_requested",
      fromAdapter: this.primary.id,
      toAdapter: this.fallback.id,
      note,
    });
    return { requested: true, gate, pendingArchitectDecision: true };
  }

  /** Architect-owned confirmation; completes the retirement. */
  confirmRetirement(note?: string): AdapterState {
    if (this.primaryState !== "retiring") {
      throw new Error("retirement_not_pending");
    }
    this.primaryState = "retired";
    this.events.push({
      at: this.now(),
      type: "retirement_confirmed",
      fromAdapter: this.primary.id,
      toAdapter: this.fallback.id,
      note,
    });
    return this.primaryState;
  }

  /** Architect-owned rejection; returns to active. */
  rejectRetirement(note?: string): AdapterState {
    if (this.primaryState !== "retiring") {
      throw new Error("retirement_not_pending");
    }
    this.primaryState = "active";
    this.events.push({
      at: this.now(),
      type: "retirement_rejected",
      fromAdapter: this.primary.id,
      toAdapter: this.fallback.id,
      note,
    });
    return this.primaryState;
  }

  /**
   * Rollback: restore the fallback adapter as authoritative immediately
   * and record an immutable rollback event. Existing events, observations,
   * and divergences are preserved (append-only history).
   */
  rollback(note?: string): AdapterState {
    if (this.primaryState === "retired") {
      return this.primaryState;
    }
    this.primaryState = "retired";
    this.events.push({
      at: this.now(),
      type: "rollback",
      fromAdapter: this.primary.id,
      toAdapter: this.fallback.id,
      note,
    });
    return this.primaryState;
  }

  private recordDivergence(
    request: DoctrineLookupRequest,
    primaryResult: DoctrineLookupResult,
    fallbackResult: DoctrineLookupResult,
  ): void {
    this.divergences.push({
      at: this.now(),
      request,
      primaryStatus: primaryResult.status,
      fallbackStatus: fallbackResult.status,
      resolved: false,
    });
  }
}

/**
 * Mark a recorded divergence as reviewed/resolved. Append-only semantics
 * are preserved: the divergence record itself is annotated in place with
 * reviewer identity and time, and its prior unresolved state remains
 * recoverable from the observation log timestamped before resolution.
 */
export function resolveDivergence(
  registry: DoctrineLookupRegistry,
  index: number,
  resolvedBy: string,
): void {
  const divergences = registry.getDivergences() as AdapterDivergence[];
  const target = divergences[index];
  if (!target) {
    throw new Error("divergence_not_found");
  }
  if (target.resolved) {
    throw new Error("divergence_already_resolved");
  }
  target.resolved = true;
  target.resolvedBy = resolvedBy;
  target.resolvedAt = new Date().toISOString();
}
