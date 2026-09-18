/**
 * calendar — TypeSpec contract for the Calendar primitive and Session object.
 *
 * Model-only (write-queue precedent): no HTTP routes, no verbs. Accumulation
 * and consolidation are the semantics; transport, storage, and emission are
 * deliberately absent pending roundtable answers (design post a330914e):
 *
 *   Q1  storage/Type-collapse fit — DDL pre-stage waits on this thread;
 *   Q3  emitters (timer shim, boot shim) decide envelope wiring;
 *   Q5  Session addressing ownership (wind-srv overlap) decides routes.
 *
 * Until then this namespace carries the shared vocabulary only: Calendar,
 * CalendarEvent, Session, AgentRef. Consumers (Java/Python/TS codegen) are
 * generated from this single contract so interop holds by construction.
 */
import "./models.tsp";

namespace org.nexus.calendar {
  // Intentionally route-free. Model-only protocol contract.
}
