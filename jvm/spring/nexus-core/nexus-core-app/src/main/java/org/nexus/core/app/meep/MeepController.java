package org.nexus.core.app.meep;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.time.Clock;
import java.util.List;
import java.util.Map;
import org.nexus.core.meep.MeepPipeline;
import org.nexus.core.meep.ast.Ast;
import org.nexus.core.meep.classifier.IrlClassifier;
import org.nexus.core.meep.compiler.ResolverCompiler;
import org.nexus.core.meep.execution.Execution;
import org.nexus.core.meep.lowering.Lowering;
import org.nexus.core.meep.model.CerLog;
import org.nexus.core.meep.model.Models;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Spring REST deployment profile over the MEEP kernel (third surface;
 * the Python reference and the Java CLI are independent, co-equal
 * deployment profiles of the same contracts).
 *
 * The adapter holds NO pipeline logic — it translates HTTP ⇄ kernel calls.
 * All stations are deterministic and side-effect-free: these endpoints
 * never touch the database, the filesystem, or the network, so exposing
 * them in the read-only monolith does not alter its posture.
 *
 * Routes are namespaced under /api/meep.
 */
@RestController
@RequestMapping("/api/meep")
public class MeepController {

    public record CompileRequest(@NotBlank String prompt) {}

    public record ClassifyResponse(Map<String, Double> probabilities, String archetype, double confidence,
                                   List<String> alternatives, String classifierVersion) {}

    public record GraphResponse(List<NodeView> nodes, List<String[]> edges, List<String> topologicalOrder,
                                String schemaVersion, String frozenAt, String contentHash) {
        public record NodeView(String id, String label, String handler) {}
    }

    public record EventsResponse(String executionId, int eventCount, String tailHash, List<EventView> events) {
        public record EventView(String eventId, String timestamp, String executionId, String nodeId,
                                String eventType, Map<String, Object> payload, String prevEventHash) {}
    }

    public record StateResponse(Map<String, String> nodeStates, List<String> completedNodes,
                                List<String> failedNodes, int eventCount, boolean complete) {}

    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of("status", "ok", "kernel", "nexus-core-meep", "version", "0.1.0");
    }

    /** Station 1–2: classify a prompt (IRL distribution + deterministic selection). */
    @PostMapping(value = "/classify", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ClassifyResponse> classify(@Valid @RequestBody CompileRequest request) {
        Ast.Features features = Ast.features(Ast.parse(request.prompt()));
        var result = IrlClassifier.classify(request.prompt(), features);
        var selection = ResolverCompiler.resolve(result);
        return ResponseEntity.ok(new ClassifyResponse(result.probabilities(), selection.archetype(),
                selection.confidence(), selection.alternatives(), result.classifierVersion()));
    }

    /** Stations 1–4: prompt → frozen ExecutionGraph (lowering + fingerprint). */
    @PostMapping(value = "/compile", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<GraphResponse> compile(@Valid @RequestBody CompileRequest request) {
        var selection = ResolverCompiler.resolve(IrlClassifier.classify(request.prompt(),
                Ast.features(Ast.parse(request.prompt()))));
        Models.ExecutionGraph graph = Lowering.lower(ResolverCompiler.compile(selection, request.prompt()), Clock.systemUTC());
        return ResponseEntity.ok(toGraph(graph));
    }

    /** Stations 1–5: prompt → CER event log. */
    @PostMapping(value = "/execute", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<EventsResponse> execute(@Valid @RequestBody CompileRequest request) {
        CerLog log = MeepPipeline.run(request.prompt());
        return ResponseEntity.ok(toEvents(log));
    }

    /** Station 6: replay a supplied event log (or a freshly executed one) to ExecutionState. */
    @PostMapping(value = "/replay", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<StateResponse> replay(@RequestBody(required = false) ReplayRequest request) {
        List<Models.CerEvent> events;
        if (request == null || request.events() == null || request.events().isEmpty()) {
            events = MeepPipeline.run(request == null || request.prompt() == null ? "" : request.prompt()).events();
        } else {
            events = request.events().stream()
                    .map(e -> new Models.CerEvent(e.eventId(), e.timestamp(), e.executionId(), e.nodeId(),
                            e.eventType(), e.payload() == null ? Map.of() : e.payload(), ""))
                    .toList();
        }
        Models.ExecutionState state = Execution.replay(events);
        return ResponseEntity.ok(new StateResponse(state.nodeStates(), state.completedNodes(),
                state.failedNodes(), state.eventCount(), state.complete()));
    }

    public record ReplayRequest(String prompt, List<EventInput> events) {
        public record EventInput(String eventId, String timestamp, String executionId, String nodeId,
                                 String eventType, Map<String, Object> payload) {}
    }

    private static GraphResponse toGraph(Models.ExecutionGraph graph) {
        return new GraphResponse(
                graph.nodes().stream().map(n -> new GraphResponse.NodeView(n.id(), n.label(), n.handler())).toList(),
                graph.edges(), graph.topologicalOrder(), graph.schemaVersion(), graph.frozenAt(), graph.contentHash());
    }

    private static EventsResponse toEvents(CerLog log) {
        return new EventsResponse(
                log.events().isEmpty() ? "" : log.events().get(0).executionId(),
                log.size(), log.tailHash(),
                log.events().stream().map(e -> new EventsResponse.EventView(e.eventId(), e.timestamp(),
                        e.executionId(), e.nodeId(), e.eventType(), e.payload(), e.prevEventHash())).toList());
    }
}
