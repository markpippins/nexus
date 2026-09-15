/**
 * solscript-facade — TypeSpec Models (Moleculer REST facade)
 *
 * HTTP request/response + error models for the future Moleculer REST
 * facade over the SOLScript TypeScript core (@nexus/solscript). The
 * library's domain models are REUSED by cross-import from the P1
 * contract (../python/models.tsp) — single source of truth, no
 * duplication. This file adds only the HTTP envelope shapes.
 *
 * Contract status: the facade SERVICE is not implemented yet (the
 * library core is). Every op is marked `Status: planned` — do not flip
 * to `implemented` until the Moleculer service exists and reconciles.
 */
import "../python/models.tsp";

using org.nexus.solscript;

namespace org.nexus.solscriptfacade {

  // ── Error envelope ───────────────────────────────────────────────────

  /** Machine-readable failure codes for facade actions. */
  union SolErrorCode {
    propositionNotFound: "PROPOSITION_NOT_FOUND",
    ruleNotFound: "RULE_NOT_FOUND",
    entityNotFound: "ENTITY_NOT_FOUND",
    transitionNotFound: "TRANSITION_NOT_FOUND",
    conceptNotFound: "CONCEPT_NOT_FOUND",
    attributeNotFound: "ATTRIBUTE_NOT_FOUND",
    contextRequired: "CONTEXT_REQUIRED",
    contextMismatch: "CONTEXT_MISMATCH",
    unknownFrameDimension: "UNKNOWN_FRAME_DIMENSION",
    evaluationError: "EVALUATION_ERROR",
    queryError: "QUERY_ERROR",
  }

  /** Typed failure body for non-2xx facade responses. */
  model SolScriptError {
    code: SolErrorCode;
    message: string;
    /** Deterministic library errors are not retryable; EVALUATION_ERROR
        may be retryable when caused by a transient backend fault. */
    retryable: boolean;
    /** Facade service instance that produced the error (diagnostics). */
    service?: string;
  }

  // ── Action envelopes ─────────────────────────────────────────────────

  /** POST /api/solscript/evaluate-proposition request body. */
  model EvaluationRequest {
    propositionId: string;
    /** Frame context (v31/v32). Required when the proposition is framed. */
    context?: Record<JsonValue>;
    /** Library evaluation options. */
    options?: EvaluationOptions;
  }

  /** POST /api/solscript/evaluate-proposition response body. */
  model EvaluationResponse {
    propositionId: string;
    disposition: Disposition;
    value?: boolean;
    groundingStatus?: string;
    evaluatedAt: utcDateTime;
    /** 'not_scoped' | 'context_required' | 'context_mismatch' | 'scoped'.
        When a context gate blocked evaluation, disposition is null. */
    contextStatus: string;
    reason?: string;
  }

  /** POST /api/solscript/check-rule request body. */
  model RuleCheckRequest {
    ruleId: string;
    entityId: string;
  }

  model RuleCheckResponse {
    ruleId: string;
    entityId: string;
    passed: boolean;
    reason: string;
  }

  /** POST /api/solscript/check-transition-guard request body. */
  model GuardCheckRequest {
    transitionId: string;
    entityId: string;
  }

  model GuardCheckResponse {
    transitionId: string;
    entityId: string;
    passed: boolean;
    /** snake_case guard results (rule_id/rule_name/passed/reason) — the
        exact shape that flows into KeychainEvent read_sets. */
    failedGuards: Record<JsonValue>[];
  }

  /** POST /api/solscript/transition-entity request body. */
  model TransitionRequest {
    entityId: string;
    transitionId: string;
    actor?: string;
    /** Caller-supplied event identity for outbox idempotency. */
    sourceEventId?: string;
    correlationId?: string;
  }

  model TransitionResponse {
    entityId: string;
    transitionId: string;
    /** 'committed' | 'refused' | 'rejected' */
    outcome: TransitionOutcome;
    stateAfter?: JsonValue;
    results: Record<JsonValue>[];
    /** The durable transition event (present for committed/refused/rejected). */
    event?: KeychainEvent;
  }

  /** POST /api/solscript/execute-query request body. */
  model QueryRequest {
    query: SolQuerySpec;
  }

  model QueryResponse {
    rows: Record<JsonValue>[];
    count: int32;
  }

  /** GET /api/solscript/health response body. */
  model SolFacadeHealthResponse {
    status: string;
    service: string;
    /** Loaded-interpreter stats (concepts, entities, propositions counts). */
    loaded: Record<JsonValue>;
  }
}
