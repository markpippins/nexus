/**
 * solscript-facade — TypeSpec Operations (Moleculer REST facade)
 *
 * REST surface for the future Moleculer service over the SOLScript
 * TypeScript core. Conventions (typescript-conventions.md + the
 * moleculer search precedent):
 *   - one op per route, flat full-path @route
 *   - contract status is the FIRST line of every op @doc:
 *     `Status: implemented | planned | deprecated | retired`
 *   - actions map 1:1 onto SolInterpreterOperation
 *     (../python/models.tsp)
 *
 * All ops are `Status: implemented`: the Moleculer service exists
 * (moleculer/solscript). Read actions are live; transition-entity returns
 * 405 at the REST tier — the readonly-by-default distribution posture
 * (the interpreter library fully supports transitions).
 */
import "@typespec/http";
import "./models.tsp";

using Http;

namespace org.nexus.solscriptfacade {

  // ========================================================================
  // Interpreter actions (1:1 with SolInterpreterOperation)
  // ========================================================================

  @route("/api/solscript/evaluate-proposition")
  @post
  @doc("""
    Status: implemented.
    Evaluate a proposition (with optional frame-context discipline).
    Maps to SolInterpreterApi.evaluateProposition. A context gate
    (context_required / context_mismatch) returns 422 with a typed
    SolScriptError; unknown frame dimensions surface as
    UNKNOWN_FRAME_DIMENSION. Successful evaluation returns the
    disposition even when it is Rejected.
  """)
  op evaluateProposition(@body body: EvaluationRequest): EvaluationResponse | SolScriptError;

  @route("/api/solscript/check-rule")
  @post
  @doc("""
    Status: implemented.
    Check a single rule against an entity.
    Maps to SolInterpreterApi.checkRule. Unknown rule/entity → 404 with
    RULE_NOT_FOUND / ENTITY_NOT_FOUND.
  """)
  op checkRule(@body body: RuleCheckRequest): RuleCheckResponse | SolScriptError;

  @route("/api/solscript/check-transition-guard")
  @post
  @doc("""
    Status: implemented.
    Evaluate a transition's guards plus the concept's invariants.
    Maps to SolInterpreterApi.checkTransitionGuard. Unknown transition →
    404 TRANSITION_NOT_FOUND.
  """)
  op checkTransitionGuard(@body body: GuardCheckRequest): GuardCheckResponse | SolScriptError;

  @route("/api/solscript/transition-entity")
  @post
  @doc("""
    Status: implemented.
    Attempt a governed state transition. Maps to
    SolInterpreterApi.transitionEntity. Never throws on guard failure:
    returns 200 with outcome=refused/rejected and the durable
    KeychainEvent — negative governed outcomes are evidence. Only
    infrastructure faults produce 5xx.
    REST-tier posture: the Moleculer facade (moleculer/solscript) DISALLOWS
    this at the HTTP layer (405 — readonly-by-default). The interpreter
    library fully supports transitions; the 405 unlocks to a JetStream write
    when the write path lands.
  """)
  op transitionEntity(@body body: TransitionRequest): TransitionResponse | SolScriptError;

  @route("/api/solscript/execute-query")
  @post
  @doc("""
    Status: implemented.
    Execute a fluent-query descriptor against a concept.
    Maps to SolInterpreterApi.executeQuery. Unknown concept → 404
    CONCEPT_NOT_FOUND; malformed filter → 422 QUERY_ERROR.
  """)
  op executeQuery(@body body: QueryRequest): QueryResponse | SolScriptError;

  // ========================================================================
  // Health
  // ========================================================================

  @route("/api/solscript/health")
  @get
  @doc("""
    Status: implemented.
    Health check including loaded-interpreter stats.
  """)
  op health(): SolFacadeHealthResponse;
}
