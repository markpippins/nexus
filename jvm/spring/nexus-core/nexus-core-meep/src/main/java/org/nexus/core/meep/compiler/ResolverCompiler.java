package org.nexus.core.meep.compiler;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.nexus.core.meep.model.Models;

/**
 * Stations 2 + 3: deterministic IR resolution and archetype-to-work-graph
 * compilation (Python reference: meep.ir_resolver / meep.spec_compiler).
 * Tiebreaker is alphabetical; threshold 0.4; templates are frozen per archetype.
 */
public final class ResolverCompiler {
    private static final double THRESHOLD = 0.4;
    private static final Map<String, List<String[]>> TEMPLATES = templates();
    private ResolverCompiler() {}

    public static Models.IrSelection resolve(Models.IrlResult result) {
        if (result.probabilities().isEmpty()) return new Models.IrSelection("REJECT", 0, List.of());
        var winner = result.probabilities().entrySet().stream().sorted((a, b) -> {
            int probability = Double.compare(b.getValue(), a.getValue());
            return probability != 0 ? probability : a.getKey().compareTo(b.getKey());
        }).findFirst().orElseThrow();
        if (winner.getValue() < THRESHOLD) return new Models.IrSelection("REJECT", winner.getValue(), List.of());
        List<String> alternatives = result.probabilities().entrySet().stream()
                .filter(e -> !e.getKey().equals(winner.getKey()) && e.getValue() >= THRESHOLD)
                .map(Map.Entry::getKey).sorted().toList();
        return new Models.IrSelection(winner.getKey(), winner.getValue(), alternatives);
    }

    public static Models.WorkRequestGraph compile(Models.IrSelection selection, String prompt) {
        if (selection.archetype().equals("REJECT")) return new Models.WorkRequestGraph(List.of(), List.of(), Map.of("archetype", "REJECT", "prompt", prompt));
        List<String[]> steps = TEMPLATES.getOrDefault(selection.archetype(), TEMPLATES.get("DEFAULT"));
        List<Models.WorkNode> nodes = new ArrayList<>();
        List<Models.WorkEdge> edges = new ArrayList<>();
        String prefix = selection.archetype().toLowerCase();
        for (int i = 0; i < steps.size(); i++) {
            String id = prefix + "-" + steps.get(i)[0];
            nodes.add(new Models.WorkNode(id, steps.get(i)[1], selection.archetype(), List.of(), List.of()));
            if (i > 0) edges.add(new Models.WorkEdge(nodes.get(i - 1).id(), id, "depends_on"));
        }
        return new Models.WorkRequestGraph(List.copyOf(nodes), List.copyOf(edges), Map.of("archetype", selection.archetype(), "prompt", prompt));
    }

    private static Map<String, List<String[]>> templates() {
        Map<String, List<String[]>> m = new LinkedHashMap<>();
        m.put("CONSTRUCTION", steps(new String[][]{{"specify", "Specify"}, {"build", "Build"}, {"verify", "Verify"}}));
        m.put("EXECUTION", steps(new String[][]{{"prepare", "Prepare"}, {"execute", "Execute"}, {"collect", "Collect results"}}));
        m.put("REFLECTION", steps(new String[][]{{"gather", "Gather context"}, {"analyze", "Analyze"}, {"report", "Document findings"}}));
        m.put("RECONCILIATION", steps(new String[][]{{"identify", "Identify conflicts"}, {"propose", "Propose resolution"}, {"apply", "Apply reconciliation"}}));
        m.put("REVISION", steps(new String[][]{{"identify", "Identify issue"}, {"plan", "Plan change"}, {"apply", "Apply change"}, {"verify", "Verify fix"}}));
        m.put("COUNTERFACTUAL", steps(new String[][]{{"scenario", "Define scenario"}, {"explore", "Explore alternative"}, {"compare", "Compare outcomes"}}));
        m.put("AUDIT", steps(new String[][]{{"collect", "Collect evidence"}, {"evaluate", "Evaluate compliance"}, {"report", "Report findings"}}));
        m.put("COMPRESSION", steps(new String[][]{{"scan", "Scan input"}, {"extract", "Extract key points"}, {"summary", "Produce summary"}}));
        m.put("CONSTRAINT_INJECTION", steps(new String[][]{{"analyze", "Analyze constraints"}, {"modify", "Modify behavior"}, {"validate", "Validate"}}));
        m.put("DEFAULT", steps(new String[][]{{"clarify", "Clarify intent"}}));
        return Map.copyOf(m);
    }
    private static List<String[]> steps(String[][] values) { return List.of(values); }
}
