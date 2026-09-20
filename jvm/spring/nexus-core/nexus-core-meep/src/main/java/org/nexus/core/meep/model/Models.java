package org.nexus.core.meep.model;

import java.util.List;
import java.util.Map;

/**
 * Immutable boundary contracts for the MEEP pipeline (Python reference:
 * meep.models). Field names intentionally mirror the Python dataclasses;
 * Java-idiomatic accessors are derived. All records are immutable — the
 * Python mutable-then-freeze ExecutionGraph lifecycle is represented by
 * construction (a lowered graph is born frozen).
 */
public final class Models {
    private Models() {}

    /** Station 1 output: probability mass over frozen archetypes. */
    public record IrlResult(Map<String, Double> probabilities, String rawInput, String classifierVersion) {}

    /** Station 2 output: deterministic argmax selection (or REJECT). */
    public record IrSelection(String archetype, double confidence, List<String> alternatives) {}

    /** Station 3 output unit of work. */
    public record WorkNode(String id, String label, String archetype, List<String> inputs, List<String> outputs) {}

    /** Station 3 dependency/trigger edge. */
    public record WorkEdge(String sourceId, String targetId, String relation) {}

    /** Station 3 output: mutable-structural work graph (pre-freeze). */
    public record WorkRequestGraph(List<WorkNode> nodes, List<WorkEdge> edges, Map<String, Object> metadata) {}

    /** Station 4 frozen execution node (handler resolved, config immutable). */
    public record ExecNode(String id, String label, String handler, Map<String, Object> config) {}

    /**
     * Station 4 output: frozen execution graph. Born immutable — equivalent
     * to the Python post-{@code _freeze()} state; contentHash is the
     * canonical-serialization fingerprint of the content fields.
     */
    public record ExecutionGraph(List<ExecNode> nodes, List<String[]> edges,
                                 List<String> topologicalOrder, String schemaVersion, String frozenAt,
                                 String contentHash) {}

    /** Station 5 event (append-only once inside a CerLog). */
    public record CerEvent(String eventId, String timestamp, String executionId, String nodeId,
                           String eventType, Map<String, Object> payload, String prevEventHash) {}

    /** Station 6 output: replayed execution state. */
    public record ExecutionState(Map<String, String> nodeStates, List<String> completedNodes,
                                 List<String> failedNodes, int eventCount, boolean complete) {}
}
