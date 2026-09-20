package org.nexus.core.meep;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
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
 * Focused kernel tests mirroring the Python reference's acceptance
 * criteria (meep/tests): classifier boundaries, AST versioning, graph
 * lowering, cycle refusal, CER chain continuity, pipeline determinism,
 * canonical JSON byte shape, partial replay.
 */
class MeepKernelTest {
    private static final Clock FIXED = Clock.fixed(Instant.parse("2026-09-20T00:00:00Z"), ZoneOffset.UTC);

    @Test
    void classifierMatchesReferenceBoundaryCases() {
        var greeting = IrlClassifier.classify("hello world", null);
        assertEquals("heuristic-v1", greeting.classifierVersion());
        assertTrue(greeting.probabilities().get("DEFAULT") >= 0.9);

        var revision = IrlClassifier.classify("fix the bug in ServiceBroker", null);
        assertTrue(revision.probabilities().get("REVISION") >= 0.5);

        var reflection = IrlClassifier.classify("why did this happen", null);
        assertTrue(reflection.probabilities().get("REFLECTION") >= 0.5);
    }

    @Test
    void probabilitiesSumToOneAcrossPrompts() {
        for (String prompt : List.of("hello world", "fix the bug", "why did this happen", "build a new service",
                "run the deployment", "compress the log file", "what if we tried this", "audit the database",
                "constrain the input size", "", "merge these two branches")) {
            double total = IrlClassifier.classify(prompt, null).probabilities().values().stream()
                    .mapToDouble(Double::doubleValue).sum();
            assertTrue(Math.abs(total - 1.0) < 0.001, "prompt '" + prompt + "' summed to " + total);
        }
    }

    @Test
    void astFeaturesAndClassifierVersionMatchReference() {
        String text = "# Build the service\n\n```java\nclass X {}\n```";
        Ast.Features features = Ast.features(Ast.parse(text));
        assertTrue(features.structural());
        assertTrue(features.hasHeadings());
        assertTrue(features.hasCodeBlocks());
        assertEquals("heuristic-v1+ast", IrlClassifier.classify(text, features).classifierVersion());
        // Short paragraph-only prompt keeps the baseline version (gating rule).
        assertEquals("heuristic-v1", IrlClassifier.classify("hello world", Ast.features(Ast.parse("hello world"))).classifierVersion());
    }

    @Test
    void resolverCompilerAndLoweringProduceRevisionGraph() {
        Models.IrSelection selection = ResolverCompiler.resolve(IrlClassifier.classify("fix the bug", null));
        assertEquals("REVISION", selection.archetype());
        Models.WorkRequestGraph work = ResolverCompiler.compile(selection, "fix the bug");
        Models.ExecutionGraph graph = Lowering.lower(work, FIXED);
        assertEquals(List.of("revision-identify", "revision-plan", "revision-apply", "revision-verify"), graph.topologicalOrder());
        assertEquals("identify_issue_handler", graph.nodes().get(0).handler());
        assertEquals("2026-09-20T00:00:00Z", graph.frozenAt());
    }

    @Test
    void cycleIsRejectedAtFreezeBoundary() {
        var graph = new Models.WorkRequestGraph(
                List.of(new Models.WorkNode("a", "A", "DEFAULT", List.of(), List.of()),
                        new Models.WorkNode("b", "B", "DEFAULT", List.of(), List.of())),
                List.of(new Models.WorkEdge("a", "b", "depends_on"), new Models.WorkEdge("b", "a", "depends_on")),
                Map.of());
        assertThrows(MeepException.class, () -> Lowering.lower(graph, FIXED));
    }

    @Test
    void cerLogIsAppendOnlyAndHashChained() {
        CerLog log = new CerLog();
        log.append(new Models.CerEvent("e1", "t1", "x", "n", "NODE_START", Map.of(), "ignored"));
        log.append(new Models.CerEvent("e2", "t2", "x", "n", "NODE_COMPLETE", Map.of(), "ignored"));
        assertEquals("genesis", log.events().get(0).prevEventHash());
        assertEquals(CanonicalJson.sha256(Map.of(
                "event_id", "e1", "timestamp", "t1", "execution_id", "x", "node_id", "n",
                "event_type", "NODE_START", "payload", Map.of(), "prev_event_hash", "genesis")),
                log.events().get(1).prevEventHash());
        assertEquals(2, log.size());
    }

    @Test
    void fullPipelineSchedulesAndReplaysDeterministically() {
        CerLog first = MeepPipeline.run("build a service", FIXED, false);
        CerLog second = MeepPipeline.run("build a service", FIXED, false);
        assertEquals(first.events(), second.events());
        assertEquals(6, first.size());
        Models.ExecutionState state = Execution.replay(first);
        assertTrue(state.complete());
        assertEquals(3, state.completedNodes().size());
        assertEquals(6, state.eventCount());
    }

    @Test
    void partialReplayMatchesPythonSemantics() {
        List<Models.CerEvent> events = MeepPipeline.run("build a service", FIXED, false).events();
        // After 1 event: first node RUNNING, not complete.
        var afterStart = Execution.replayUntil(events, 1);
        assertEquals("RUNNING", afterStart.nodeStates().get("construction-specify"));
        assertTrue(!afterStart.complete());
        // After 2 events: first node COMPLETED and the window is terminal-complete.
        var afterComplete = Execution.replayUntil(events, 2);
        assertEquals("COMPLETED", afterComplete.nodeStates().get("construction-specify"));
        assertTrue(afterComplete.complete());
        // n = 0 equals empty replay; n = size equals full replay.
        assertEquals(0, Execution.replayUntil(events, 0).eventCount());
        assertEquals(Execution.replay(events), Execution.replayUntil(events, events.size()));
    }

    @Test
    void mixedFailSkipStatesAreTerminal() {
        CerLog log = new CerLog();
        log.append(new Models.CerEvent("s1", "t", "x", "n1", "NODE_START", Map.of(), ""));
        log.append(new Models.CerEvent("f1", "t", "x", "n1", "NODE_FAIL", Map.of(), ""));
        log.append(new Models.CerEvent("s2", "t", "x", "n2", "NODE_START", Map.of(), ""));
        log.append(new Models.CerEvent("k2", "t", "x", "n2", "NODE_SKIP", Map.of(), ""));
        Models.ExecutionState state = Execution.replay(log);
        assertEquals("FAILED", state.nodeStates().get("n1"));
        assertEquals("SKIPPED", state.nodeStates().get("n2"));
        assertTrue(state.complete());
        assertEquals(List.of("n1"), state.failedNodes());
    }

    @Test
    void canonicalJsonMatchesPythonDumpShape() {
        assertEquals("{\"a\": 1, \"b\": 2}", CanonicalJson.write(Map.of("b", 2, "a", 1)));
        assertEquals("[1, 2, 3]", CanonicalJson.write(List.of(1, 2, 3)));
        assertEquals("null", CanonicalJson.write(new Object[]{null}[0]));
    }
}
