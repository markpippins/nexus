/**
 * solscript — TypeSpec Operations
 *
 * SOLScript is an in-memory interpreter (library), not an HTTP service.
 * Model-only contract (nats-envelope precedent): the reconciler must find
 * ZERO routes here — matching the reference, which has zero HTTP routes in
 * python/SOLScript. The programmatic interpreter surface is documented as
 * models in models.tsp (see SolInterpreterApi and its request/response
 * models); a Moleculer REST facade will declare its own operations over
 * those models and is out of scope for this contract.
 */
import "./models.tsp";

namespace org.nexus.solscript {
  // Intentionally route-free. See models.tsp → SolInterpreterApi.
}
