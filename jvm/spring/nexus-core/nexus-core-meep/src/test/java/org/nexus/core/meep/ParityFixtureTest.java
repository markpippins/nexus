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
import org.junit.jupiter.api.Test;
import org.nexus.core.meep.classifier.IrlClassifier;
import org.nexus.core.meep.compiler.ResolverCompiler;
import org.nexus.core.meep.execution.Execution;
import org.nexus.core.meep.lowering.Lowering;

/**
 * Verifies the checked-in parity fixtures (frozen boundary cases from the
 * Python reference's acceptance criteria) against the Java kernel.
 */
class ParityFixtureTest {
    private static final Clock FIXED = Clock.fixed(Instant.parse("2026-09-20T00:00:00Z"), ZoneOffset.UTC);

    @Test
    void fixtureBoundaryCasesRemainStable() throws IOException {
        JsonNode fixture;
        try (InputStream stream = getClass().getResourceAsStream("/meep-parity-fixtures.json")) {
            if (stream == null) throw new IOException("parity fixture resource missing");
            fixture = new ObjectMapper().readTree(stream);
        }
        for (JsonNode row : fixture.get("classifier")) {
            var result = IrlClassifier.classify(row.get("prompt").asText(), null);
            var selection = ResolverCompiler.resolve(result);
            assertEquals(row.get("expected_archetype").asText(), selection.archetype());
            assertTrue(result.probabilities().get(selection.archetype()) >= row.get("min_probability").asDouble());
        }
        var pipeline = fixture.get("pipeline");
        var selection = ResolverCompiler.resolve(IrlClassifier.classify(pipeline.get("prompt").asText(), null));
        var graph = Lowering.lower(ResolverCompiler.compile(selection, pipeline.get("prompt").asText()), FIXED);
        var log = Execution.schedule(graph, FIXED);
        assertEquals(pipeline.get("expected_event_count").asInt(), log.size());
        assertEquals(pipeline.get("expected_node_ids").size(), graph.nodes().size());
    }
}
