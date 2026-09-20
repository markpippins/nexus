package org.nexus.core.meep.execution;

import java.time.Clock;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.nexus.core.meep.MeepException;
import org.nexus.core.meep.MeepTimestamp;
import org.nexus.core.meep.model.CerLog;
import org.nexus.core.meep.model.Models;

/**
 * Stations 5 + 6: deterministic scheduler (topological walk, simulated
 * handlers, hash-chained events) and pure-function replay reducer
 * (Python reference: meep.scheduler / meep.replay_engine). Clock is
 * injectable for deterministic tests.
 */
public final class Execution {
    private Execution() {}

    public static CerLog schedule(Models.ExecutionGraph graph, Clock clock) {
        CerLog log = new CerLog();
        String executionId = "ex-" + graph.contentHash().substring(0, 12);
        int counter = 0;
        for (String nodeId : graph.topologicalOrder()) {
            Models.ExecNode node = graph.nodes().stream().filter(n -> n.id().equals(nodeId)).findFirst().orElseThrow();
            String startId = String.format("evt-%s-%04d", executionId, ++counter);
            log.append(new Models.CerEvent(startId, MeepTimestamp.now(clock), executionId, nodeId,
                    "NODE_START", Map.of("handler", node.handler()), ""));
            String completeId = String.format("evt-%s-%04d", executionId, ++counter);
            log.append(new Models.CerEvent(completeId, MeepTimestamp.now(clock), executionId, nodeId,
                    "NODE_COMPLETE", Map.of("status", "ok", "node_id", nodeId, "handler", "simulated"), ""));
        }
        return log;
    }

    public static Models.ExecutionState replay(CerLog log) { return replay(log.events()); }

    public static Models.ExecutionState replay(List<Models.CerEvent> events) {
        Map<String, String> states = new LinkedHashMap<>();
        List<String> completed = new ArrayList<>();
        List<String> failed = new ArrayList<>();
        for (Models.CerEvent event : events) {
            switch (event.eventType()) {
                case "NODE_START" -> states.put(event.nodeId(), "RUNNING");
                case "NODE_COMPLETE" -> { states.put(event.nodeId(), "COMPLETED"); completed.add(event.nodeId()); }
                case "NODE_FAIL" -> { states.put(event.nodeId(), "FAILED"); failed.add(event.nodeId()); }
                case "NODE_SKIP" -> states.put(event.nodeId(), "SKIPPED");
                default -> throw new MeepException("Unknown CER event type: " + event.eventType());
            }
        }
        boolean complete = !states.isEmpty() && states.values().stream().allMatch(s -> List.of("COMPLETED", "FAILED", "SKIPPED").contains(s));
        return new Models.ExecutionState(Map.copyOf(states), List.copyOf(completed), List.copyOf(failed), events.size(), complete);
    }

    /** Partial replay up to (exclusive) event index n (Python: replay_until). */
    public static Models.ExecutionState replayUntil(List<Models.CerEvent> events, int n) {
        return replay(events.subList(0, Math.min(n, events.size())));
    }
}
