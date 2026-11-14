/**
 * agenda — TypeSpec contract for the deliberation surface.
 *
 * Model-only: no routes, no verbs. The contract describes `nebula.agendas` and
 * `nebula.agenda_items`; it does not own their storage. Transport and emitters
 * are deliberately absent — the deliberation writer is nebula-srv, and the
 * read surfaces are the existing views.
 *
 * Distinct from assembly.forum_agendas (UI grouping). See models.tsp.
 */
import "./models.tsp";

namespace org.nexus.agenda {
  // Intentionally route-free. Model-only protocol contract.
}
