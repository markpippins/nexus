package org.nexus.core.aegis.kernel;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Validate a revision-scoped bridge and build the deterministic Wind graph
 * plan — port of typescript/aegis-srv/src/wind-compiler.ts.
 *
 * <p>The bridge deliberately consumes pre-existing Wind tasks/outcomes
 * referenced by immutable mapping rows; it plans the workflow version, nodes,
 * and edges. Task/outcome provisioning remains a separate design-time
 * operation because those rows carry Wind ownership/title/agent semantics
 * that Aegis must not invent.
 *
 * <p>Pure: no DB, no I/O — usable for dry-runs and hermetic tests.
 */
public final class WindCompiler {

    private WindCompiler() {}

    public record RevisionState(String id, String name, Boolean isInitial, Boolean isTerminal) {}

    public record RevisionTransition(String id, String name, String fromStateId, String toStateId) {}

    public record WindTaskMapping(String stateId, String windTaskId, boolean isCheckOnly) {}

    public record WindOutcomeMapping(String transitionId, String windTaskId, String windOutcomeId) {}

    public record WindTaskRef(String id, String name, String tackleTaskId) {}

    public record WindOutcomeRef(String id, String taskId, String code) {}

    public record WindNodePlan(String stateId, String windTaskId, String name,
                               boolean isEntrypoint, boolean isTerminal) {}

    public record WindEdgePlan(String transitionId, String fromNodeStateId, String fromTaskId,
                               String outcomeId, String toNodeStateId) {}

    public record WindCompilationPlan(List<WindNodePlan> nodes, List<WindEdgePlan> edges,
                                      String graphDigest, List<String> errors) {}

    public static WindCompilationPlan buildWindCompilationPlan(
            String revisionId,
            List<RevisionState> states,
            List<RevisionTransition> transitions,
            List<WindTaskMapping> taskMappings,
            List<WindOutcomeMapping> outcomeMappings,
            List<WindTaskRef> windTasks,
            List<WindOutcomeRef> windOutcomes) {

        List<String> errors = new ArrayList<>();
        Map<String, RevisionState> stateById = new LinkedHashMap<>();
        Map<String, WindTaskMapping> taskMappingByState = new LinkedHashMap<>();
        Map<String, WindOutcomeMapping> outcomeMappingByTransition = new LinkedHashMap<>();
        Map<String, WindTaskRef> taskById = new LinkedHashMap<>();
        Map<String, WindOutcomeRef> outcomeById = new LinkedHashMap<>();
        for (WindTaskRef t : windTasks) taskById.put(t.id(), t);
        for (WindOutcomeRef o : windOutcomes) outcomeById.put(o.id(), o);

        for (RevisionState state : states) {
            if (state.id() == null || state.name() == null) {
                errors.add("revision contains a state without an id and name");
                continue;
            }
            if (stateById.containsKey(state.id())) {
                errors.add("revision contains duplicate state " + state.id());
            }
            stateById.put(state.id(), state);
        }

        for (WindTaskMapping mapping : taskMappings) {
            if (taskMappingByState.containsKey(mapping.stateId())) {
                errors.add("multiple Wind task mappings exist for state " + mapping.stateId());
            }
            taskMappingByState.put(mapping.stateId(), mapping);
            if (!stateById.containsKey(mapping.stateId())) {
                errors.add("task mapping references state " + mapping.stateId()
                        + " outside revision " + revisionId);
            }
            if (!taskById.containsKey(mapping.windTaskId())) {
                errors.add("task mapping references missing Wind task " + mapping.windTaskId());
            }
            WindTaskRef task = taskById.get(mapping.windTaskId());
            boolean expectedCheckOnly = task != null && task.tackleTaskId() == null;
            if (task != null && mapping.isCheckOnly() != expectedCheckOnly) {
                errors.add("task mapping for state " + mapping.stateId()
                        + " disagrees with Wind task " + mapping.windTaskId()
                        + " check-only semantics");
            }
        }

        for (RevisionState state : states) {
            if (!taskMappingByState.containsKey(state.id())) {
                errors.add("state " + state.name() + " (" + state.id()
                        + ") has no revision-scoped Wind task mapping");
            }
        }

        for (WindOutcomeMapping mapping : outcomeMappings) {
            if (outcomeMappingByTransition.containsKey(mapping.transitionId())) {
                errors.add("multiple Wind outcome mappings exist for transition " + mapping.transitionId());
            }
            outcomeMappingByTransition.put(mapping.transitionId(), mapping);
            RevisionTransition transition = transitions.stream()
                    .filter(t -> t.id().equals(mapping.transitionId())).findFirst().orElse(null);
            if (transition == null) {
                errors.add("outcome mapping references transition " + mapping.transitionId()
                        + " outside revision " + revisionId);
            }
            WindOutcomeRef outcome = outcomeById.get(mapping.windOutcomeId());
            if (outcome == null) {
                errors.add("outcome mapping references missing Wind outcome " + mapping.windOutcomeId());
            } else if (!outcome.taskId().equals(mapping.windTaskId())) {
                errors.add("outcome " + mapping.windOutcomeId()
                        + " does not belong to mapped task " + mapping.windTaskId());
            }
            if (!taskById.containsKey(mapping.windTaskId())) {
                errors.add("outcome mapping references missing Wind task " + mapping.windTaskId());
            }
        }

        List<WindNodePlan> nodes = new ArrayList<>();
        for (RevisionState state : states) {
            WindTaskMapping mapping = taskMappingByState.get(state.id());
            nodes.add(new WindNodePlan(state.id(),
                    mapping != null ? mapping.windTaskId() : "",
                    state.name(),
                    Boolean.TRUE.equals(state.isInitial()),
                    Boolean.TRUE.equals(state.isTerminal())));
        }

        List<RevisionState> initialStates = states.stream()
                .filter(s -> Boolean.TRUE.equals(s.isInitial())).toList();
        if (initialStates.size() != 1) {
            errors.add("Wind compilation requires exactly one initial state; found "
                    + initialStates.size());
        }

        List<WindEdgePlan> edges = new ArrayList<>();
        for (RevisionTransition transition : transitions) {
            if (transition.fromStateId() == null || transition.toStateId() == null) {
                errors.add("transition " + transition.name() + " (" + transition.id()
                        + ") must have both from_state_id and to_state_id");
                continue;
            }
            if (!stateById.containsKey(transition.fromStateId())
                    || !stateById.containsKey(transition.toStateId())) {
                errors.add("transition " + transition.name() + " (" + transition.id()
                        + ") references a state outside revision " + revisionId);
                continue;
            }
            WindTaskMapping taskMapping = taskMappingByState.get(transition.fromStateId());
            WindOutcomeMapping outcomeMapping = outcomeMappingByTransition.get(transition.id());
            if (taskMapping == null || outcomeMapping == null) {
                if (outcomeMapping == null) {
                    errors.add("transition " + transition.name() + " (" + transition.id()
                            + ") has no revision-scoped Wind outcome mapping");
                }
                continue;
            }
            if (!outcomeMapping.windTaskId().equals(taskMapping.windTaskId())) {
                errors.add("transition " + transition.name() + " (" + transition.id()
                        + ") outcome task does not match its source state task");
                continue;
            }
            edges.add(new WindEdgePlan(transition.id(), transition.fromStateId(),
                    taskMapping.windTaskId(), outcomeMapping.windOutcomeId(), transition.toStateId()));
        }

        List<Map<String, Object>> nodeMaps = new ArrayList<>();
        for (WindNodePlan n : nodes) {
            nodeMaps.add(AegisDigest.obj(
                    "state_id", n.stateId(), "wind_task_id", n.windTaskId(), "name", n.name(),
                    "is_entrypoint", n.isEntrypoint(), "is_terminal", n.isTerminal()));
        }
        List<Map<String, Object>> edgeMaps = new ArrayList<>();
        for (WindEdgePlan e : edges) {
            edgeMaps.add(AegisDigest.obj(
                    "transition_id", e.transitionId(), "from_node_state_id", e.fromNodeStateId(),
                    "from_task_id", e.fromTaskId(), "outcome_id", e.outcomeId(),
                    "to_node_state_id", e.toNodeStateId()));
        }
        Map<String, Object> graphDigestInput = AegisDigest.obj(
                "revision_id", revisionId, "nodes", nodeMaps, "edges", edgeMaps);

        // Dedupe errors preserving order (TS Set preserves insertion order)
        Set<String> deduped = new LinkedHashSet<>(errors);
        return new WindCompilationPlan(nodes, edges, AegisDigest.digestJson(graphDigestInput),
                new ArrayList<>(deduped));
    }
}
