/**
 * jev-inquiry — TypeSpec Models
 *
 * Bounded judgment contract for TypeSafe System One (jev) integration
 * with the SOLScript evaluation chain via the WR `inquiry?` shape.
 *
 * Ratified by: Architect decision (this contract) + Analyst acceptance.
 * Guardrails (non-negotiable):
 *   - Jev is advisory judgment ONLY: no admission authority, no reviewer
 *     settlement, no direct Resolution/PEB mutation.
 *   - PEB may prioritize candidates using Jev outputs; it must NOT admit.
 *   - Adapter must NOT settle review — uncertainty escalates to human/role
 *     review via the witnessed-run adjudication queue.
 *   - Raw judgments are reusable (cacheable) but never authority.
 *
 * Reference: Architect assessment `1564cb0a` (TypeSafe System One in Nexus).
 * Source reviewed: ~/dev/github/typesafe-ai (Python SDK, system-one-adapter-python,
 * vLLM serving fork, skill design docs). Model: jev-latest (released 2026-09-15).
 */
namespace org.nexus.jev.inquiry {

  // ── TypeSafe primitives (from TypeSafe SDK) ─────────────────────────

  /**
   * TypeSafe's three judgment primitives.
   *   Noul   → P(yes) in [0, 1] — proposition truth
   *   Choice → per-label probabilities + confidence — classification with
   *            built-in "flag for review" when uncertain
   *   Score  → rubric → probability-weighted expected value + confidence +
   *            legend — graded rubric evaluation
   */
  union JevPrimitive {
    noul:   "noul",
    choice: "choice",
    score:  "score",
  }

  /** Confidence is concentration-based (calibrated, not vibes). */
  model JevConfidence {
    /** Concentration parameter; higher = more concentrated = more confident. */
    concentration: float64;
    /** Derived probability mass in the decision region. */
    decisionMass: float64;
  }

  /** Noul result: proposition truth probability. */
  model NoulResult {
    primitive: "noul";
    /** P(yes) in [0, 1]. */
    probability: float64;
    confidence: JevConfidence;
  }

  /** Choice result: per-label probabilities + confidence. */
  model ChoiceResult {
    primitive: "choice";
    /** Label → probability mapping. */
    labelProbabilities: Record<float64>;
    /** Label with highest probability. */
    topLabel: string;
    confidence: JevConfidence;
    /** True when confidence is below review threshold — maps to adjudication queue. */
    flagForReview: boolean;
  }

  /** Score result: rubric evaluation. */
  model ScoreResult {
    primitive: "score";
    /** Probability-weighted expected value from rubric. */
    expectedValue: float64;
    confidence: JevConfidence;
    /** Human-readable legend mapping score bands to interpretations. */
    legend: Record<string>;
  }

  /** Union of all TypeSafe judgment outcomes. */
  union JevOutcome {
    noul:   NoulResult,
    choice: ChoiceResult,
    score:  ScoreResult,
  }

  /** Outcome that could NOT be produced (uncertain, stale, refused, unevaluable). */
  union JevNonOutcome {
    /** Confidence below threshold — must enqueue for human/role review. */
    uncertain:   "uncertain",
    /** Read-set drifted; judgment cache stale; must not be silently served. */
    stale:       "stale",
    /** Explicit refusal (e.g., policy violation, out of scope). */
    refused:     "refused",
    /** Cannot evaluate with available evidence/backend. */
    unevaluable: "unevaluable",
  }

  // ── Calibration & provenance metadata ───────────────────────────────

  /** Adapter/backend identity for judgment cache key and provenance. */
  union AdapterBackend {
    ollamaHelium:     "ollama-helium",
    jevHosted:        "jev-hosted",
    jevSelfHosted:    "jev-self-hosted",
    customProvider:   "custom-provider",
  }

  /** Calibration metadata for jev vs ollama agreement measurement. */
  model CalibrationMetadata {
    /** Agreement rate on the benchmark corpus (0–1). */
    agreementRate: float64;
    /** Calibration error (ECE or similar). */
    calibrationError?: float64;
    /** Latency percentile (ms). */
    latencyP50?: int64;
    latencyP95?: int64;
    /** Cost per judgment (USD or token estimate). */
    costPerJudgment?: float64;
    /** Cache hit/reuse rate. */
    cacheHitRate?: float64;
  }

  /** Judgment cache key (per architect assessment: state hash + question + model version + policy version). */
  model JudgmentCacheKey {
    /** SHA-256 of the evaluation frame state. */
    stateHash: string;
    /** The bounded question text. */
    question: string;
    /** Expected outcome type (noul|choice|score). */
    expectedOutcomeType: JevPrimitive;
    /** TypeSafe model version (e.g., "jev-latest", "jev-2026-09-15"). */
    modelVersion: string;
    /** Policy/vocabulary revision hash. */
    policyVersionHash: string;
    /** Adapter/backend identity. */
    adapterBackend: AdapterBackend;
  }

  /** Cached judgment record (raw judgments reusable, never authority). */
  model CachedJudgment {
    key: JudgmentCacheKey;
    /** The judgment outcome or non-outcome. */
    outcome: JevOutcome | JevNonOutcome;
    /** When this judgment was produced. */
    producedAt: utcDateTime;
    /** Calibration metadata at production time. */
    calibration: CalibrationMetadata;
    /** Full provenance for replay. */
    provenance: JevProvenance;
  }

  /** Full provenance for replay and audit. */
  model JevProvenance {
    /** Source system: "type-safe-system-one" or "system-one-adapter". */
    sourceSystem: string;
    /** Verifier method: "concentration" | "rubric" | "adapter-llm". */
    verifierMethod: string;
    /** Policy version hash at evaluation time. */
    policyVersionHash: string;
    /** Read-set manifest digest (from Keychains). */
    readSetDigest?: string;
    /** Evaluator/backend identity. */
    evaluatorRef: string;
    /** Adapter backend used. */
    adapterBackend: AdapterBackend;
  }

  // ── The WR `inquiry?` block shape (ratification 1ba40aa0) ───────────

  /**
   * The WR `inquiry?` block — bounded evaluation contract.
   * A bounded TypeSafe evaluation IS an inquiry with these exact fields.
   * Inherits witnessed-run/attempt semantics: evaluations become witnessed,
   * provenance-durable work, not fire-and-forget API calls.
   */
  model JevInquiry {
    /** The bounded judgment question (proposition to evaluate). */
    question: string;

    /** Expected outcome primitive: noul | choice | score. */
    expectedOutcomeType: JevPrimitive;

    /** Evidence/read-set requirements (state fields, frame scope). */
    evidenceRequirements: JevEvidenceRequirements;

    /** Read-set scope — the evaluation frame. */
    readSetScope: JevReadSetScope;

    /** Evaluator/backend identity: "system_one/jev" or adapter alias. */
    evaluatorRef: string;

    /** TypeSafe model version (e.g., "jev-latest"). */
    modelVersion: string;

    /** Policy/vocabulary revision hash. */
    policyVersion: string;

    /** Calibration metadata (agreement, ECE, latency, cost, cache reuse). */
    calibrationMetadata?: CalibrationMetadata;

    /** Explicit non-outcome handling. */
    nonOutcomeHandling: JevNonOutcomeHandling;

    /** Idempotency key for the inquiry (inquiry? contract). */
    idempotencyKey: string;

    /** Evaluation mode (from Context Model). */
    evaluationMode?: "deterministic" | "hybrid" | "inference";

    /** Confidence threshold for uncertain flag (from Context Model). */
    confidenceThreshold?: float64;

    /** Reasoner chain for hybrid/inference mode (from Context Model). */
    reasonerChain?: string[];
  }

  /** Evidence/read-set requirements. */
  model JevEvidenceRequirements {
    /** State fields required for the judgment. */
    requiredStateFields: string[];

    /** Optional frame dimension constraints. */
    frameDimensions?: string[];

    /** Minimum evidence completeness threshold (0–1). */
    evidenceCompletenessThreshold?: float64;
  }

  /** Read-set scope — the evaluation frame. */
  model JevReadSetScope {
    /** Frame dimension commitments. */
    frameValues: Record<string>;

    /** Provenance context (source_system, verifier_method, policy_version_hash). */
    provenanceContext: JevProvenance;

    /** Optional entity/concept being evaluated. */
    subjectEntityId?: string;
    subjectConceptId?: string;
  }

  /** Non-outcome handling configuration. */
  model JevNonOutcomeHandling {
    /** Action on uncertain: "enqueue_review" | "fail_visible" | "return_unresolved". */
    onUncertain: "enqueue_review" | "fail_visible" | "return_unresolved";

    /** Action on stale read-set: "reject" | "retry_fresh" | "return_stale". */
    onStale: "reject" | "retry_fresh" | "return_stale";

    /** Action on refused: "fail_visible" | "return_refusal". */
    onRefused: "fail_visible" | "return_refusal";

    /** Action on unevaluable: "fail_visible" | "return_unresolved". */
    onUnevaluable: "fail_visible" | "return_unresolved";
  }

  /** Inquiry result — witnessed, provenance-durable. */
  model JevInquiryResult {
    /** The original inquiry. */
    inquiry: JevInquiry;

    /** The judgment outcome or non-outcome. */
    result: JevOutcome | JevNonOutcome;

    /** Full provenance. */
    provenance: JevProvenance;

    /** Witnessed-run attempt metadata. */
    attempt: JevAttemptMetadata;

    /** Cache key for raw judgment reuse. */
    cacheKey: JudgmentCacheKey;
  }

  /** Witnessed-run attempt metadata (from inquiry? contract). */
  model JevAttemptMetadata {
    attemptId: string;
    workerId: string;
    startedAt: utcDateTime;
    completedAt?: utcDateTime;
    status: "succeeded" | "failed" | "timed_out";
    error?: string;
    exitCode?: int32;
  }

  // ── Guardrails (enforced by contract, not convention) ───────────────

  /**
   * Guardrail declarations — these are CONTRACTUAL constraints, not
   * documentation. Implementations MUST enforce at the code level.
   */
  model JevGuardrails {
    /** Jev outputs are advisory only — never admission authority. */
    noAdmissionAuthority: true;

    /** Jev outputs never settle reviewer disposition. */
    noReviewerSettlement: true;

    /** Jev outputs never directly mutate Resolution/PEB state. */
    noDirectResolutionPebMutation: true;

    /** PEB may use Jev for prioritization only. */
    pebMayPrioritizeOnly: true;

    /** Uncertainty MUST escalate to witnessed-run adjudication queue. */
    uncertaintyEscalatesToAdjudication: true;

    /** Stale/drifted read-sets MUST NOT be silently served from cache. */
    staleReadSetsRefusedFromCache: true;

    /** Jev vs ollama agreement MUST be measured before hosted commitment. */
    calibrationRequiredBeforeAdoption: true;
  }

  // ── SolScript seam (InferenceEngine.external_knowledge_base) ─────────

  /**
   * The SOLScript seam — already exists as a stub in
   * python/SOLScript/solscript/inference_engine.py:
   *
   *   InferenceEngine.external_knowledge_base: KnowledgeBase | None
   *   KnowledgeBase.query(target_key, context) → Any
   *
   * This contract maps JevInquiry to the KnowledgeBase query protocol.
   */
  model SolScriptJevSeam {
    /** The unbounded query key (proposition ID or question hash). */
    queryKey: string;

    /** Context from the evaluation frame (state, frame values, provenance). */
    context: Record<unknown>;

    /** Expected return type maps to JevPrimitive. */
    expectedReturnType: JevPrimitive;

    /** The full inquiry for provenance/replay. */
    inquiry: JevInquiry;
  }
}