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

  // ── Judgment Cache Design (Jev 4) ────────────────────────────────────

  /**
   * Cache entry status — tracks freshness and validity.
   */
  enum CacheEntryStatus {
    /** Fresh entry, valid for reuse. */
    Fresh: "fresh",

    /** Read-set has drifted; entry must not be served. */
    StaleReadSet: "stale_read_set",

    /** Model version mismatch; entry invalidated. */
    ModelVersionMismatch: "model_version_mismatch",

    /** Policy/vocabulary revision mismatch; entry invalidated. */
    PolicyVersionMismatch: "policy_version_mismatch",

    /** Adapter/backend changed; entry invalidated. */
    BackendMismatch: "backend_mismatch",

    /** Explicitly evicted (retention policy). */
    Evicted: "evicted",

    /** Entry corrupted or failed validation. */
    Corrupted: "corrupted",
  }

  /**
   * Cache validation result — determines if cached entry can be served.
   */
  model CacheValidationResult {
    /** Whether the entry is valid for reuse. */
    valid: boolean;

    /** If invalid, the reason. */
    status?: CacheEntryStatus;

    /** Human-readable detail. */
    detail: string;

    /** The cache key that was validated. */
    validatedKey: JudgmentCacheKey;

    /** Current read-set digest for comparison. */
    currentReadSetDigest?: string;

    /** Expected read-set digest from cached entry. */
    expectedReadSetDigest?: string;
  }

  /**
   * Cache eviction/retention policy.
   */
  model CacheRetentionPolicy {
    /** Maximum time-to-live for cached judgments. */
    maxTtl: string;  // ISO 8601 duration, e.g., "P30D"

    /** Maximum number of entries per (state_hash, question) group. */
    maxEntriesPerGroup?: int32;

    /** Maximum total cache size (entries). */
    maxTotalEntries?: int32;

    /** Evict on model version change. */
    evictOnModelVersionChange: boolean = true;

    /** Evict on policy version change. */
    evictOnPolicyVersionChange: boolean = true;

    /** Evict on backend change. */
    evictOnBackendChange: boolean = true;
  }

  /**
   * Stale read-set rejection configuration.
   */
  model StaleReadSetRejection {
    /** Enable stale read-set detection. */
    enabled: boolean = true;

    /** Maximum allowed age of read-set (ISO 8601 duration). */
    maxReadSetAge: string = "P1D";

    /** Reject if read-set digest doesn't match cached digest. */
    rejectOnDigestMismatch: boolean = true;

    /** Action when stale: "reject" | "retry_fresh" | "return_stale_with_warning". */
    onStale: "reject" | "retry_fresh" | "return_stale_with_warning" = "reject";
  }

  /**
   * Replay identity — ensures cached judgments are replayable with identical results.
   */
  model ReplayIdentity {
    /** Unique replay identifier. */
    replayId: string;

    /** The cache key used for the original judgment. */
    originalCacheKey: JudgmentCacheKey;

    /** The cached judgment being replayed. */
    originalJudgment: CachedJudgment;

    /** Current context for replay (may differ from original). */
    replayContext: Record<unknown>;

    /** Whether replay produced identical outcome. */
    outcomeIdentical: boolean;

    /** If not identical, the difference summary. */
    difference?: string;

    /** Timestamp of replay attempt. */
    replayedAt: utcDateTime;
  }

  /**
   * Source-of-truth placement for judgment records.
   * Judgments are L1 evidence (never authority) — stored in the evidence store,
   * not in the canonical resolution schema.
   */
  enum JudgmentStoreLocation {
    /** Evidence store (append-only, immutable) — PRIMARY. */
    EvidenceStore: "evidence_store",

    /** Keychains read-set manifest (for provenance linkage). */
    KeychainsManifest: "keychains_manifest",

    /** Witnessed-run attempt record (for audit trail). */
    WitnessedRun: "witnessed_run",

    /** Cache store (ephemeral, TTL-based) — SECONDARY. */
    CacheStore: "cache_store",
  }

  /**
   * Canonical judgment record — the authoritative record of a judgment.
   * Stored in evidence store; cache is a derived projection.
   */
  model CanonicalJudgmentRecord {
    /** Unique record ID. */
    recordId: string;

    /** The cache key (also serves as lookup key). */
    cacheKey: JudgmentCacheKey;

    /** The judgment outcome or non-outcome. */
    outcome: JevOutcome | JevNonOutcome;

    /** Full provenance. */
    provenance: JevProvenance;

    /** Calibration metadata. */
    calibration: CalibrationMetadata;

    /** Source-of-truth placement. */
    storeLocation: JudgmentStoreLocation;

    /** Cache validation status at write time. */
    validationStatus: CacheEntryStatus = CacheEntryStatus.Fresh;

    /** Created timestamp. */
    createdAt: utcDateTime;

    /** Retention policy reference. */
    retentionPolicy: CacheRetentionPolicy;

    /** Replay identity for verification. */
    replayIdentity?: ReplayIdentity;
  }

  /**
   * Negative test case — documents a scenario that MUST produce a specific
   * cache validation failure. Used for contract compliance testing.
   */
  model JudgmentCacheNegativeCase {
    /** Unique case ID. */
    caseId: string;

    /** Human-readable description. */
    description: string;

    /** The cache key that triggers the failure. */
    triggeringKey: JudgmentCacheKey;

    /** The current context that causes the failure. */
    currentContext: Record<unknown>;

    /** Expected validation failure. */
    expectedFailure: CacheEntryStatus;

    /** Expected error message pattern. */
    expectedErrorPattern: string;

    /** Category of negative case. */
    category: "stale_read_set" | "model_version_mismatch" | "policy_version_mismatch" | "backend_mismatch" | "corrupted_entry";
  }

  /**
   * Standard negative cases for Jev cache compliance.
   * Implementations MUST handle all these cases correctly.
   */
  model StandardNegativeCases {
    staleReadSet: JudgmentCacheNegativeCase;
    modelVersionMismatch: JudgmentCacheNegativeCase;
    policyVersionMismatch: JudgmentCacheNegativeCase;
    backendMismatch: JudgmentCacheNegativeCase;
    corruptedEntry: JudgmentCacheNegativeCase;
  }

  /**
   * The standard negative cases that every Jev cache implementation MUST handle.
   * These are the compliance test vectors.
   */
  const STANDARD_NEGATIVE_CASES: StandardNegativeCases = #{
    staleReadSet: #{
      caseId: "neg-stale-readset-001",
      description: "Read-set has drifted since cache entry was created — digest mismatch",
      triggeringKey: #{
        stateHash: "a1b2c3d4e5f67890",
        question: "Is the entity's readiness_score greater than 0.85?",
        expectedOutcomeType: "noul",
        modelVersion: "jev-latest",
        policyVersionHash: "policy-v1",
        adapterBackend: "ollama-helium",
      },
      currentContext: #{
        readSetDigest: "different-digest-12345",
        expectedReadSetDigest: "original-digest-67890",
      },
      expectedFailure: CacheEntryStatus.StaleReadSet,
      expectedErrorPattern: "read.set.digest.mismatch|stale.read.set",
      category: "stale_read_set",
    },
    modelVersionMismatch: #{
      caseId: "neg-model-version-001",
      description: "Model version changed from jev-latest to jev-2026-10-01 — cache invalidated",
      triggeringKey: #{
        stateHash: "a1b2c3d4e5f67890",
        question: "Classify the drift_type for the observed entity divergence.",
        expectedOutcomeType: "choice",
        modelVersion: "jev-latest",
        policyVersionHash: "policy-v1",
        adapterBackend: "ollama-helium",
      },
      currentContext: #{
        modelVersion: "jev-2026-10-01",
      },
      expectedFailure: CacheEntryStatus.ModelVersionMismatch,
      expectedErrorPattern: "model.version.mismatch",
      category: "model_version_mismatch",
    },
    policyVersionMismatch: #{
      caseId: "neg-policy-version-001",
      description: "Policy/vocabulary revision changed — cache invalidated",
      triggeringKey: #{
        stateHash: "a1b2c3d4e5f67890",
        question: "Evaluate the overall readiness of the evaluation frame against the standard rubric.",
        expectedOutcomeType: "score",
        modelVersion: "jev-latest",
        policyVersionHash: "policy-v1",
        adapterBackend: "ollama-helium",
      },
      currentContext: #{
        policyVersionHash: "policy-v2",
      },
      expectedFailure: CacheEntryStatus.PolicyVersionMismatch,
      expectedErrorPattern: "policy.version.mismatch",
      category: "policy_version_mismatch",
    },
    backendMismatch: #{
      caseId: "neg-backend-mismatch-001",
      description: "Adapter backend changed from ollama-helium to jev-hosted — cache invalidated",
      triggeringKey: #{
        stateHash: "a1b2c3d4e5f67890",
        question: "Determine the readiness tier for the current evaluation frame.",
        expectedOutcomeType: "choice",
        modelVersion: "jev-latest",
        policyVersionHash: "policy-v1",
        adapterBackend: "ollama-helium",
      },
      currentContext: #{
        adapterBackend: "jev-hosted",
      },
      expectedFailure: CacheEntryStatus.BackendMismatch,
      expectedErrorPattern: "backend.mismatch|adapter.backend.changed",
      category: "backend_mismatch",
    },
    corruptedEntry: #{
      caseId: "neg-corrupted-001",
      description: "Cached entry failed validation (checksum mismatch or malformed data)",
      triggeringKey: #{
        stateHash: "a1b2c3d4e5f67890",
        question: "Score the representation quality against the canonical representation.",
        expectedOutcomeType: "score",
        modelVersion: "jev-latest",
        policyVersionHash: "policy-v1",
        adapterBackend: "ollama-helium",
      },
      currentContext: #{
        validationError: "checksum_mismatch",
      },
      expectedFailure: CacheEntryStatus.Corrupted,
      expectedErrorPattern: "corrupted|validation.failed|checksum",
      category: "corrupted_entry"
    },
    };
}
