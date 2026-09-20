package org.nexus.core.meep;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.nexus.core.meep.ast.Ast;
import org.nexus.core.meep.classifier.IrlClassifier;
import org.nexus.core.meep.compiler.ResolverCompiler;
import org.nexus.core.meep.execution.Execution;
import org.nexus.core.meep.lowering.Lowering;
import org.nexus.core.meep.model.CerLog;
import org.nexus.core.meep.model.Models;
import org.nexus.core.meep.serialization.CanonicalJson;

/**
 * Byte-exact cross-runtime parity against Python-generated golden vectors
 * (meep-golden-vectors.json, produced by generate-golden-fixtures.py from
 * the reference implementation with pinned clocks).
 *
 * Proves, per vector:
 *   1. CanonicalJson.write reproduces Python json.dumps(sort_keys=True)
 *      byte-for-byte — including ensure_ascii escaping.
 *   2. ExecutionGraph.content_hash matches the Python digest
 *      (nodes/edges/topological order/schema/timestamp all feed it).
 *   3. The CER hash chain links and tail hash match event-for-event.
 *   4. Replay semantics match the Python reducer, including partial windows.
 */
class MeepGoldenParityTest {
    private static final Clock GOLDEN = Clock.fixed(Instant.parse("2026-09-20T00:00:00Z"), ZoneOffset.UTC);
    private static JsonNode vectors;

    @BeforeAll
    static void loadVectors() throws IOException {
        try (InputStream stream = MeepGoldenParityTest.class.getResourceAsStream("/meep-golden-vectors.json")) {
            if (stream == null) throw new IOException("meep-golden-vectors.json missing from test resources");
            vectors = new ObjectMapper().readTree(stream);
        }
    }

    // ---- 1. canonical JSON byte parity --------------------------------

    @Test
    void canonicalJsonIsByteIdenticalToPython() {
        for (JsonNode vector : vectors.get("canonical_json_bytes")) {
            String java = CanonicalJson.write(toJavaValue(vector.get("value")));
            assertEquals(vector.get("expected_json").asText(), java,
                    "canonical byte mismatch for case: " + vector.get("label").asText());
            assertEquals(vector.get("expected_sha256").asText(), CanonicalJson.sha256(toJavaValue(vector.get("value"))));
        }
    }

    /** Convert a fixture JSON tree into the Java object shape CanonicalJson receives. */
    private static Object toJavaValue(JsonNode node) {
        if (node.isNull()) return null;
        if (node.isBoolean()) return node.asBoolean();
        if (node.isInt() || node.isLong()) return node.asLong();
        if (node.isFloatingPointNumber()) return node.asDouble();
        if (node.isTextual()) return node.asText();
        if (node.isArray()) {
            List<Object> out = new ArrayList<>();
            node.forEach(child -> out.add(toJavaValue(child)));
            return out;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        node.properties().forEach(entry -> out.put(entry.getKey(), toJavaValue(entry.getValue())));
        return out;
    }

    // ---- 2 + 3. per-archetype graph hashes and CER chains -------------

    @Test
    void graphContentHashesMatchPythonForEveryArchetype() {
        for (JsonNode vector : vectors.get("graphs")) {
            String prompt = vector.get("prompt").asText();
            Models.ExecutionGraph graph = lowerReference(prompt);
            assertEquals(vector.get("expected_content_hash").asText(), graph.contentHash(),
                    "content_hash drift for prompt: " + prompt);
            assertEquals(vector.get("frozen_at").asText(), graph.frozenAt());
            List<String> expectedOrder = new ArrayList<>();
            vector.get("topological_order").forEach(n -> expectedOrder.add(n.asText()));
            assertEquals(expectedOrder, graph.topologicalOrder());
            // handler names are hash inputs — assert them too
            List<String> expectedHandlers = new ArrayList<>();
            vector.get("nodes").forEach(n -> expectedHandlers.add(n.get("handler").asText()));
            for (int i = 0; i < expectedHandlers.size(); i++) {
                assertEquals(expectedHandlers.get(i), graph.nodes().get(i).handler(), "handler drift: " + prompt);
            }
        }
    }

    @Test
    void cerChainsMatchPythonEventForEvent() {
        for (JsonNode vector : vectors.get("cer_chains")) {
            CerLog log = MeepPipeline.run(vector.get("prompt").asText(), GOLDEN, true);
            JsonNode expectedEvents = vector.get("events");
            assertEquals(expectedEvents.size(), log.size(), "event count drift: " + vector.get("prompt").asText());
            for (int i = 0; i < log.size(); i++) {
                Models.CerEvent event = log.events().get(i);
                JsonNode expected = expectedEvents.get(i);
                assertEquals(expected.get("event_id").asText(), event.eventId());
                assertEquals(expected.get("event_type").asText(), event.eventType());
                assertEquals(expected.get("node_id").asText(), event.nodeId());
                assertEquals(expected.get("prev_event_hash").asText(), event.prevEventHash(), "chain link drift at " + i);
                assertEquals(expected.get("expected_hash_of_event").asText(), CanonicalJson.sha256(Map.of(
                        "event_id", event.eventId(), "timestamp", event.timestamp(), "execution_id", event.executionId(),
                        "node_id", event.nodeId(), "event_type", event.eventType(),
                        "payload", event.payload(), "prev_event_hash", event.prevEventHash())));
            }
            assertEquals(vector.get("expected_tail_hash").asText(), log.tailHash());
        }
    }

    // ---- 4. replay semantics parity -----------------------------------

    @Test
    void replaySemanticsMatchPythonReducer() {
        JsonNode vector = vectors.get("replay").get(0);
        CerLog log = new CerLog();
        for (JsonNode event : vector.get("events")) {
            log.append(new Models.CerEvent(event.get("event_id").asText(), "2026-09-20T00:00:00Z", "ex-mixed",
                    event.get("node_id").asText(), event.get("event_type").asText(), Map.of(), ""));
        }
        JsonNode expected = vector.get("expected_state");

        Models.ExecutionState state = Execution.replay(log);
        Map<String, String> expectedStates = new LinkedHashMap<>();
        expected.get("node_states").properties().forEach(e -> expectedStates.put(e.getKey(), e.getValue().asText()));
        assertEquals(expectedStates, state.nodeStates());
        assertEquals(expected.get("event_count").asInt(), state.eventCount());
        assertEquals(expected.get("is_complete").asBoolean(), state.complete());
        assertEquals(List.of("n0"), state.completedNodes());
        assertEquals(List.of("n1"), state.failedNodes());

        // partial window: after 5 events n2 is still RUNNING, not complete
        Models.ExecutionState partial = Execution.replayUntil(log.events(), vector.get("partial_until_5").get("event_count").asInt());
        assertEquals(vector.get("partial_until_5").get("n2_state").asText(), partial.nodeStates().get("n2"));
        assertEquals(vector.get("partial_until_5").get("is_complete").asBoolean(), partial.complete());
    }

    // ---- classifier distribution parity (rounding-tolerant) -----------

    @Test
    void classifierDistributionsMatchPython() {
        for (JsonNode vector : vectors.get("classifier")) {
            Models.IrSelection selection = ResolverCompiler.resolve(
                    IrlClassifier.classify(vector.get("prompt").asText(), null));
            assertEquals(vector.get("archetype").asText(), selection.archetype(),
                    "archetype drift: " + vector.get("prompt").asText());
            assertTrue(Math.abs(vector.get("confidence").asDouble() - selection.confidence()) < 1e-9);
        }
    }

    // ---- helpers -------------------------------------------------------

    private static Models.ExecutionGraph lowerReference(String prompt) {
        Models.IrSelection selection = ResolverCompiler.resolve(IrlClassifier.classify(prompt, Ast.features(Ast.parse(prompt))));
        return Lowering.lower(ResolverCompiler.compile(selection, prompt), GOLDEN);
    }
}
