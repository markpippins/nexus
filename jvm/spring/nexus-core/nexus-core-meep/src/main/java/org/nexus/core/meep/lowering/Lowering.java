package org.nexus.core.meep.lowering;

import java.time.Clock;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.nexus.core.meep.MeepException;
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

    public static Models.ExecutionGraph lower(Models.WorkRequestGraph graph, Clock clock) {
        List<Models.ExecNode> nodes = graph.nodes().stream()
                .map(n -> new Models.ExecNode(n.id(), n.label(), handler(n.archetype(), n.id()), Map.of())).toList();
        List<String[]> edges = graph.edges().stream().map(e -> new String[]{e.sourceId(), e.targetId()}).toList();
        List<String> order = topological(nodes, edges);
        String frozenAt = nodes.isEmpty() ? "" : Instant.now(clock).toString().replace("+00:00", "Z");
        Map<String, Object> content = new LinkedHashMap<>();
        content.put("nodes", nodes); content.put("edges", edges); content.put("topological_order", order);
        content.put("schema_version", "v1"); content.put("frozen_at", frozenAt);
        return new Models.ExecutionGraph(List.copyOf(nodes), List.copyOf(edges), List.copyOf(order), "v1", frozenAt, CanonicalJson.sha256(content));
    }

    private static String handler(String archetype, String id) {
        String suffix = id.contains("-") ? id.substring(id.indexOf('-') + 1) : "";
        if ("REVISION".equals(archetype) && "identify".equals(suffix)) return "identify_issue_handler";
        if ("RECONCILIATION".equals(archetype) && "identify".equals(suffix)) return "identify_conflicts_handler";
        if ("AUDIT".equals(archetype) && "collect".equals(suffix)) return "collect_evidence_handler";
        Map<String, String> handlers = Map.ofEntries(
                Map.entry("specify", "specify_handler"), Map.entry("build", "construct_handler"), Map.entry("verify", "verify_handler"),
                Map.entry("prepare", "prepare_handler"), Map.entry("execute", "execute_handler"), Map.entry("collect", "collect_results_handler"),
                Map.entry("gather", "gather_context_handler"), Map.entry("analyze", "analyze_handler"), Map.entry("report", "report_findings_handler"),
                Map.entry("propose", "propose_resolution_handler"), Map.entry("apply", "apply_reconciliation_handler"),
                Map.entry("plan", "plan_change_handler"), Map.entry("scenario", "define_scenario_handler"), Map.entry("explore", "explore_alternative_handler"),
                Map.entry("compare", "compare_outcomes_handler"), Map.entry("evaluate", "evaluate_compliance_handler"), Map.entry("summary", "produce_summary_handler"),
                Map.entry("scan", "scan_input_handler"), Map.entry("extract", "extract_key_points_handler"), Map.entry("modify", "modify_behavior_handler"),
                Map.entry("validate", "validate_constraints_handler"), Map.entry("clarify", "clarify_intent_handler"));
        return handlers.getOrDefault(suffix, "generic_handler");
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
