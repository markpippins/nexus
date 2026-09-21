/**
 * jev-inquiry — TypeSpec Operations
 *
 * Model-only contract (nats-envelope precedent): the reconciler must find
 * ZERO routes here — matching the reference, which has zero HTTP routes in
 * the system-one-adapter-python library. The programmatic interpreter surface
 * is documented as models in models.tsp; a Moleculer REST facade or the
 * SOLScript KnowledgeBase adapter will declare its own operations over
 * these models and is out of scope for this contract.
 */
import "./models.tsp";

namespace org.nexus.jev.inquiry {
  // Intentionally route-free. See models.tsp for the programmatic surface.
}