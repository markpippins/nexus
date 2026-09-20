package org.nexus.core.meep.lowering;

import java.time.Clock;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.nexus.core.meep.MeepException;
import org.nexus.core.meep.MeepTimestamp;
import org.nexus.core.meep.model.Models;
import org.nexus.core.meep.serialization.CanonicalJson;

/**
 * Station 4: freeze boundary (Python reference: meep.lowering_pass).
 * Resolves handlers, validates edges, computes topological order via
 * Kahn's algorithm, stamps frozen_at from an injectable Clock, and
 * fingerprints the content fields with canonical JSON + SHA-256.
 * Archetype-qualified handler names mirror the Python _HANDLER_MAP.
 */
public final class Lowering {
    private Lowering() {}

    /** Python _HANDLER_MAP verbatim — (archetype, labelKey) → handler. Handler names enter content_hash. */
    private static final Map<String, Map<String, String>> HANDLER_MAP = Map.of(
            "CONSTRUCTION", Map.of("specify", "specify_handler", "build", "construct_handler", "verify", "verify_handler"),
            "EXECUTION", Map.of("prepare", "prepare_handler", "execute", "execute_handler", "collect", "collect_results_handler"),
            "REFLECTION", Map.of("gather", "gather_context_handler", "analyze", "analyze_handler", "report", "report_findings_handler"),
            "RECONCILIATION", Map.of("identify", "identify_conflicts_handler", "propose", "propose_resolution_handler", "apply", "apply_reconciliation_handler"),
            "REVISION", Map.of("identify", "identify_issue_handler", "plan", "plan_change_handler", "apply", "apply_change_handler", "verify", "verify_fix_handler"),
            "COUNTERFACTUAL", Map.of("scenario", "define_scenario_handler", "explore", "explore_alternative_handler", "compare", "compare_outcomes_handler"),
            "AUDIT", Map.of("collect", "collect_evidence_handler", "evaluate", "evaluate_compliance_handler", "report", "report_audit_findings_handler"),
            "COMPRESSION", Map.of("scan", "scan_input_handler", "extract", "extract_key_points_handler", "summary", "produce_summary_handler"),
            "CONSTRAINT_INJECTION", Map.of("analyze", "analyze_constraints_handler", "modify", "modify_behavior_handler", "validate", "validate_constraints_handler"),
            "DEFAULT", Map.of("clarify", "clarify_intent_handler"));
    private static final String GENERIC_HANDLER = "generic_handler";

    public static Models.ExecutionGraph lower(Models.WorkRequestGraph graph, Clock clock) {
        List<Models.ExecNode> nodes = graph.nodes().stream()
                .map(n -> new Models.ExecNode(n.id(), n.label(), handler(n.archetype(), n.id()), Map.of())).toList();
        List<String[]> edges = graph.edges().stream().map(e -> new String[]{e.sourceId(), e.targetId()}).toList();
        List<String> order = topological(nodes, edges);
        String frozenAt = nodes.isEmpty() ? "" : MeepTimestamp.now(clock);
        Map<String, Object> content = new LinkedHashMap<>();
        content.put("nodes", nodes); content.put("edges", edges); content.put("topological_order", order);
        content.put("schema_version", "v1"); content.put("frozen_at", frozenAt);
        return new Models.ExecutionGraph(List.copyOf(nodes), List.copyOf(edges), List.copyOf(order), "v1", frozenAt, CanonicalJson.sha256(content));
    }

    static String handler(String archetype, String id) {
        String suffix = id.contains("-") ? id.substring(id.indexOf('-') + 1) : "";
        return HANDLER_MAP.getOrDefault(archetype, Map.of()).getOrDefault(suffix, GENERIC_HANDLER);
    }

    private static List<String> topological(List<Models.ExecNode> nodes, List<String[]> edges) {
        Map<String, Integer> degree = new HashMap<>(); Map<String, List<String>> adjacent = new HashMap<>();
        nodes.forEach(n -> { degree.put(n.id(), 0); adjacent.put(n.id(), new ArrayList<>()); });
        for (String[] edge : edges) {
            if (!degree.containsKey(edge[0]) || !degree.containsKey(edge[1])) throw new MeepException("edge references unknown node");
            adjacent.get(edge[0]).add(edge[1]); degree.put(edge[1], degree.get(edge[1]) + 1);
        }
        ArrayDeque<String> queue = new ArrayDeque<>();
        degree.forEach((id, d) -> { if (d == 0) queue.add(id); });
        List<String> order = new ArrayList<>();
        while (!queue.isEmpty()) {
            String id = queue.remove(); order.add(id);
            for (String next : adjacent.get(id)) if (degree.merge(next, -1, Integer::sum) == 0) queue.add(next);
        }
        if (order.size() != nodes.size()) throw new MeepException("Graph contains a cycle");
        return order;
    }
}
