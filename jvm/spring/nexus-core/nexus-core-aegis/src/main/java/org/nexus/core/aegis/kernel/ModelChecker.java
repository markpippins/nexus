package org.nexus.core.aegis.kernel;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Deterministic state-space model checker — faithful port of
 * typescript/aegis-srv/src/model-checker.ts.
 *
 * <p>Runs a bounded, deterministic check over the structured state graph
 * (states with initial/terminal flags, transitions with from/to, invariants,
 * properties, temporal properties). Performs reachability analysis, deadlock
 * detection, unreachable-state discovery, and best-effort structural
 * invariant/property/temporal verdicts with counterexample traces. Free-form
 * guard/action/invariant text is treated structurally (identifier-reference
 * validation) — it is arbitrary TLA+ with no evaluator here.
 *
 * <p>Pure: no DB, no I/O. Unit-testable and used by both the structural
 * engine and (via the REST surface) the model-check endpoint.
 */
public final class ModelChecker {

    private ModelChecker() {}

    // ── Model shapes (JSON-shaped records, mirroring the TS interfaces) ──

    public record McState(String id, String name, boolean isInitial, boolean isTerminal) {}

    public record McTransition(String id, String name, String fromStateId, String toStateId,
                               String guardExpression, boolean weakFairness, boolean strongFairness,
                               Integer priority) {}

    public record McInvariant(String id, String name, String expression, boolean isTypeInvariant) {}

    public record McProperty(String id, String name, String type, String expression) {}

    public record McTemporalProperty(String id, String name, String operator, String expression) {}

    public record McModel(List<McState> states, List<McTransition> transitions,
                          List<McInvariant> invariants, List<McProperty> properties,
                          List<McTemporalProperty> temporalProperties,
                          List<String> variables, List<String> constants) {}

    // ── Verdicts ─────────────────────────────────────────────────────────

    public record Verdict(String kind, String name, String type, String result, String detail) {}

    public record Report(String status, List<String> reachableStates, List<String> unreachableStates,
                         List<String> deadlockTrace, List<Verdict> verdicts,
                         List<String> errors, List<String> warnings) {}

    // ── TLA+ identifier reference validation ─────────────────────────────

    private static final Set<String> TLA_RESERVED = Set.of(
            "MODULE", "CONSTANTS", "VARIABLES", "ASSUME", "THEOREM", "IN", "IF", "THEN",
            "ELSE", "CASE", "LET", "TRUE", "FALSE", "BOOLEAN", "CHOOSE", "EXCEPT",
            "DOMAIN", "UNION", "SUBSET", "LAMBDA", "Nat", "Int", "Real", "Seq", "Set",
            "NULL", "ENABLED", "UNCHANGED");

    private static final Pattern IDENTIFIER = Pattern.compile("[A-Za-z_][A-Za-z0-9_]*");
    private static final Pattern STRING_LITERAL = Pattern.compile("\"[^\"]*\"|'[^']*'");
    private static final Pattern BACKSLASH_OP = Pattern.compile("\\\\[A-Za-z]+");

    /** Extract bare identifiers from a TLA+ expression, skipping string literals and backslash-operators. */
    static List<String> identifiers(String expr) {
        if (expr == null || expr.isBlank()) return List.of();
        String cleaned = BACKSLASH_OP.matcher(STRING_LITERAL.matcher(expr).replaceAll(" ")).replaceAll(" ");
        List<String> out = new ArrayList<>();
        Matcher m = IDENTIFIER.matcher(cleaned);
        while (m.find()) {
            String s = m.group();
            if (!TLA_RESERVED.contains(s) && !"id".equals(s) && !"name".equals(s)
                    && !"type".equals(s) && !"status".equals(s)) {
                out.add(s);
            }
        }
        return out;
    }

    // ── Pure checker ─────────────────────────────────────────────────────

    public static Report checkModel(McModel model) {
        List<String> errors = new ArrayList<>();
        List<String> warnings = new ArrayList<>();
        List<Verdict> verdicts = new ArrayList<>();
        String[] status = {"success"};

        Map<String, McState> stateById = new LinkedHashMap<>();
        Map<String, String> stateNameById = new LinkedHashMap<>();
        for (McState s : model.states()) {
            stateById.put(s.id(), s);
            stateNameById.put(s.id(), s.name());
        }
        Set<String> knownNames = new LinkedHashSet<>();
        for (McState s : model.states()) knownNames.add(s.name());
        knownNames.addAll(model.variables());
        knownNames.addAll(model.constants());

        // Adjacency: stateId -> outgoing transitions. Wildcards (no from_state_id) apply everywhere.
        Map<String, List<McTransition>> outgoing = new LinkedHashMap<>();
        List<McTransition> wildcard = new ArrayList<>();
        for (McState s : model.states()) outgoing.put(s.id(), new ArrayList<>());
        for (McTransition t : model.transitions()) {
            if (t.fromStateId() != null && outgoing.containsKey(t.fromStateId())) {
                outgoing.get(t.fromStateId()).add(t);
            } else if (t.fromStateId() == null) {
                wildcard.add(t);
            }
        }

        // Structural validation
        List<McState> initial = model.states().stream().filter(McState::isInitial).toList();
        if (initial.isEmpty()) {
            status[0] = "failure";
            errors.add("no initial state declared (is_initial=false on all states)");
        }
        for (McTransition t : model.transitions()) {
            if (t.fromStateId() != null && !stateById.containsKey(t.fromStateId())) {
                errors.add("transition \"" + t.name() + "\" references missing from-state " + t.fromStateId());
            }
            if (t.toStateId() != null && !stateById.containsKey(t.toStateId())) {
                errors.add("transition \"" + t.name() + "\" references missing to-state " + t.toStateId());
            }
            if (t.fromStateId() != null && t.fromStateId().equals(t.toStateId())
                    && (t.guardExpression() == null || t.guardExpression().isBlank())) {
                warnings.add("transition \"" + t.name() + "\" is an unguarded self-loop (may not terminate)");
            }
        }

        // Reachability (BFS from all initial states)
        Set<String> reachable = new LinkedHashSet<>();
        Map<String, String> parent = new LinkedHashMap<>();
        List<String> queue = new ArrayList<>();
        for (McState s : initial) {
            reachable.add(s.id());
            parent.put(s.id(), null);
            queue.add(s.id());
        }
        for (int i = 0; i < queue.size(); i++) {
            String cur = queue.get(i);
            List<McTransition> edges = new ArrayList<>(outgoing.getOrDefault(cur, List.of()));
            edges.addAll(wildcard);
            for (McTransition t : edges) {
                String nextId = t.toStateId();
                if (nextId != null && stateById.containsKey(nextId) && !reachable.contains(nextId)) {
                    reachable.add(nextId);
                    parent.put(nextId, cur);
                    queue.add(nextId);
                }
            }
        }

        List<String> reachableNames = model.states().stream().filter(s -> reachable.contains(s.id()))
                .map(McState::name).toList();
        List<String> unreachableNames = model.states().stream().filter(s -> !reachable.contains(s.id()))
                .map(McState::name).toList();
        if (!unreachableNames.isEmpty()) {
            warnings.add("unreachable states: " + String.join(", ", unreachableNames));
        }

        // pathTo via BFS parents (state names): unshift the current node's name,
        // then walk to its parent until the chain ends at an initial state
        // (parent entry null). Yields [initial, ..., target].
        java.util.function.Function<String, List<String>> pathTo = targetId -> {
            List<String> path = new ArrayList<>();
            String cur = targetId;
            while (cur != null) {
                path.add(0, stateNameById.getOrDefault(cur, cur));
                cur = parent.get(cur);
            }
            return path;
        };

        // Deadlock detection: reachable non-terminal state with no outgoing edges
        String deadlock = null;
        for (String id : reachable) {
            McState s = stateById.get(id);
            boolean hasOut = !outgoing.getOrDefault(id, List.of()).isEmpty() || !wildcard.isEmpty();
            if (!hasOut && !s.isTerminal()) {
                deadlock = id;
                break;
            }
        }
        List<String> deadlockTrace = null;
        if (deadlock != null) {
            status[0] = "failure";
            deadlockTrace = pathTo.apply(deadlock);
            errors.add("deadlock: reachable non-terminal state \"" + stateNameById.get(deadlock)
                    + "\" has no outgoing transitions");
        }

        // Invariant checks (structural)
        for (McInvariant inv : model.invariants()) {
            if (inv.expression() == null || inv.expression().isBlank()) {
                verdicts.add(new Verdict("invariant", inv.name(), null, "FAIL", "empty invariant expression"));
                continue;
            }
            List<String> ids = identifiers(inv.expression());
            List<String> unknown = ids.stream().filter(s -> !knownNames.contains(s)).toList();
            if (!unknown.isEmpty()) {
                verdicts.add(new Verdict("invariant", inv.name(), null, "FAIL",
                        "references undefined identifier(s): " + String.join(", ", unknown)));
                if (!"failure".equals(status[0])) status[0] = "failure";
                continue;
            }
            if (inv.isTypeInvariant() && model.variables().stream().noneMatch(ids::contains)) {
                verdicts.add(new Verdict("invariant", inv.name(), null, "WARN",
                        "type invariant does not reference any declared variable (structural check cannot confirm)"));
                continue;
            }
            verdicts.add(new Verdict("invariant", inv.name(), null, "PASS",
                    "references only known identifiers (structural)"));
        }

        // Property checks (structural)
        for (McProperty prop : model.properties()) {
            List<String> referencedStates = identifiers(prop.expression()).stream()
                    .filter(knownNames::contains).toList();
            boolean referencedReachable = referencedStates.stream().allMatch(reachableNames::contains);
            switch (prop.type() == null ? "" : prop.type()) {
                case "safety" -> {
                    if (referencedStates.isEmpty()) {
                        verdicts.add(new Verdict("property", prop.name(), prop.type(), "PASS",
                                "safety property references no state (trivially structural)"));
                    } else if (referencedReachable) {
                        verdicts.add(new Verdict("property", prop.name(), prop.type(), "WARN",
                                "safety property references reachable state(s); needs TLA+ evaluation to prove"));
                    } else {
                        verdicts.add(new Verdict("property", prop.name(), prop.type(), "PASS",
                                "safety property only references unreachable state(s)"));
                    }
                }
                case "liveness" -> {
                    boolean reachableTerminal = model.states().stream()
                            .anyMatch(s -> reachable.contains(s.id()) && s.isTerminal());
                    boolean hasCycle = hasCycleInReachable(reachable, outgoing, wildcard);
                    if (referencedReachable && (reachableTerminal || hasCycle)) {
                        verdicts.add(new Verdict("property", prop.name(), prop.type(), "PASS",
                                "liveness: progress possible (reachable terminal or cycle present)"));
                    } else if (referencedReachable) {
                        verdicts.add(new Verdict("property", prop.name(), prop.type(), "FAIL",
                                "liveness: no reachable terminal and no cycle — progress cannot be satisfied"));
                        if (!"failure".equals(status[0])) status[0] = "failure";
                    } else {
                        verdicts.add(new Verdict("property", prop.name(), prop.type(), "WARN",
                                "liveness references unreachable state(s)"));
                    }
                }
                case "fairness" -> {
                    boolean hasFairness = model.transitions().stream()
                            .anyMatch(t -> t.weakFairness() || t.strongFairness());
                    verdicts.add(new Verdict("property", prop.name(), prop.type(),
                            hasFairness ? "PASS" : "WARN",
                            hasFairness ? "at least one transition declares weak/strong fairness"
                                    : "no transition declares fairness — cannot guarantee fairness"));
                }
                default -> verdicts.add(new Verdict("property", prop.name(), prop.type(), "WARN",
                        "unknown property type \"" + prop.type() + "\""));
            }
        }

        // Temporal property checks (structural)
        for (McTemporalProperty tp : model.temporalProperties()) {
            List<String> referencedStates = identifiers(tp.expression()).stream()
                    .filter(knownNames::contains).toList();
            boolean referencedReachable = referencedStates.stream().allMatch(reachableNames::contains);
            switch (tp.operator() == null ? "" : tp.operator()) {
                case "[]" -> verdicts.add(new Verdict("temporal", tp.name(), null,
                        referencedReachable ? "PASS" : "WARN",
                        referencedReachable ? "always: all referenced states are reachable (structural)"
                                : "always: references unreachable state(s)"));
                case "<>" -> {
                    if (referencedStates.isEmpty()) {
                        verdicts.add(new Verdict("temporal", tp.name(), null, "PASS",
                                "eventually: no referenced state (trivially structural)"));
                    } else {
                        verdicts.add(new Verdict("temporal", tp.name(), null,
                                referencedReachable ? "PASS" : "FAIL",
                                referencedReachable ? "eventually: referenced state is reachable"
                                        : "eventually: referenced state is NOT reachable"));
                        if (!referencedReachable && !"failure".equals(status[0])) status[0] = "failure";
                    }
                }
                case "->", "~>", "=>" -> verdicts.add(new Verdict("temporal", tp.name(), null,
                        referencedReachable ? "PASS" : "WARN",
                        referencedReachable ? "leads-to/implies: referenced states reachable (structural)"
                                : "leads-to/implies: references unreachable state(s)"));
                default -> verdicts.add(new Verdict("temporal", tp.name(), null, "WARN",
                        "unknown temporal operator \"" + tp.operator() + "\""));
            }
        }

        return new Report(status[0], reachableNames, unreachableNames, deadlockTrace, verdicts, errors, warnings);
    }

    /** Detect a cycle among reachable states (DFS over outgoing + wildcard edges). */
    private static boolean hasCycleInReachable(Set<String> reachable,
                                               Map<String, List<McTransition>> outgoing,
                                               List<McTransition> wildcard) {
        final int WHITE = 0, GRAY = 1, BLACK = 2;
        Map<String, Integer> color = new LinkedHashMap<>();
        for (String id : reachable) color.put(id, WHITE);

        for (String id : reachable) {
            if (color.get(id) != WHITE) continue;
            // Iterative DFS with explicit stack (node, edgeIndex).
            List<String> stack = new ArrayList<>();
            List<Integer> idx = new ArrayList<>();
            color.put(id, GRAY);
            stack.add(id);
            idx.add(0);
            while (!stack.isEmpty()) {
                int top = stack.size() - 1;
                String cur = stack.get(top);
                List<McTransition> edges = new ArrayList<>(outgoing.getOrDefault(cur, List.of()));
                edges.addAll(wildcard);
                if (idx.get(top) >= edges.size()) {
                    color.put(cur, BLACK);
                    stack.remove(top);
                    idx.remove(top);
                    continue;
                }
                McTransition t = edges.get(idx.get(top));
                idx.set(top, idx.get(top) + 1);
                String next = t.toStateId();
                if (next == null || !color.containsKey(next)) continue;
                int c = color.get(next);
                if (c == GRAY) return true;
                if (c == WHITE) {
                    color.put(next, GRAY);
                    stack.add(next);
                    idx.add(0);
                }
            }
        }
        return false;
    }
}
