/**
 * write-queue — TypeSpec contract for the mobile write-queue protocol surface.
 *
 * Cross-runtime contract between the Java (nexus-core) mobile write-queue
 * and the Python (cascade) reconciler. The queue is NOT a second event
 * format: it reuses the shared CanonicalEnvelope machinery (nats-envelope)
 * so there is one serialization convention. The semantic distinction lives
 * in the STREAM/lifecycle, not in a second envelope shape:
 *
 *   nexus.events      → canonical events — "what happened"
 *   nexus.write-queue → reconciliation intents — "what a disconnected or
 *                       capability-poor context wants reconciled"
 *
 * Model-only (nats-envelope precedent): no HTTP routes, no verbs. The Java
 * queue produces WriteQueueEntry entries; the Python cascade reconciler
 * consumes them and emits canonical events on nexus.events. Both are
 * generated from this single contract so interop holds by construction.
 */
import "./models.tsp";

namespace org.nexus.writequeue {
  // Intentionally route-free. This is a model-only protocol contract.
}