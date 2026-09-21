package org.nexus.core.aegis;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.nexus.core.aegis.kernel.AegisDigest;
import org.nexus.core.aegis.kernel.ModelChecker;
import org.nexus.core.aegis.kernel.TlcParser;
import org.nexus.core.aegis.kernel.WindCompiler;
import org.nexus.core.aegis.store.AegisStore;

/**
 * Kernel behavior tests for the JVM aegis port — mirrors the intent of the
 * TS model-checker / wind-compiler / tlc-parser unit surfaces. Pure, no DB.
 */
class AegisKernelTest {

    private static ModelChecker.McModel model(
            List<ModelChecker.McState> states,
            List<ModelChecker.McTransition> transitions) {
        return new ModelChecker.McModel(states, transitions, List.of(), List.of(), List.of(),
                List.of("x"), List.of("N"));
    }

    @Test
    void canonicalJsonSortsKeysRecursivelyAndPreservesArrayOrder() {
        Object value = Map.of("b", 1, "a", List.of(Map.of("y", 2, "x", 1)));
        assertThat(AegisDigest.canonicalJson(value))
                .isEqualTo("{\"a\":[{\"x\":1,\"y\":2}],\"b\":1}");
    }

    @Test
    void digestJsonMatchesTsFormat() {
        String digest = AegisDigest.digestJson(Map.of("k", "v"));
        assertThat(digest).startsWith("sha256:").hasSize("sha256:".length() + 64);
        assertThat(digest).isEqualTo(AegisDigest.digestJson(Map.of("k", "v")));
    }

    @Test
    void canonicalJsonIntegralDoublesBeyondLongRangeStayExact() {
        // JS prints integral doubles < 1e21 in full decimal form; the old
        // (long) cast truncated any integral double >= 2^53. 2^60 must render
        // exactly, not as the 2^53-wrapped value.
        double twoPow60 = Math.pow(2, 60);
        assertThat(AegisDigest.canonicalJson(Map.of("n", twoPow60)))
                .isEqualTo("{\"n\":1152921504606846976}");
        // Normal integral and fractional doubles are unchanged.
        assertThat(AegisDigest.canonicalJson(Map.of("a", 2.0d))).isEqualTo("{\"a\":2}");
        assertThat(AegisDigest.canonicalJson(Map.of("a", 2.5d))).isEqualTo("{\"a\":2.5}");
    }

    @Test
    void childColumnAllowlistsMirrorTsChildHandlers() {
        // Every TS childHandlers table must be allowlisted with identical
        // columns; unknown tables are refused before any SQL is built.
        assertThat(AegisStore.childColumns("constant"))
                .containsExactly("name", "type", "value", "description", "constraints");
        assertThat(AegisStore.childColumns("variable"))
                .contains("attribute_id");
        assertThatThrownBy(() -> AegisStore.childColumns("pg_catalog.pg_shadow"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void reachabilityAndUnreachableDiscovery() {
        ModelChecker.McState s1 = new ModelChecker.McState("s1", "red", true, false);
        ModelChecker.McState s2 = new ModelChecker.McState("s2", "green", false, true);
        ModelChecker.McState s3 = new ModelChecker.McState("s3", "orphan", false, false);
        ModelChecker.McTransition t = new ModelChecker.McTransition(
                "t1", "go", "s1", "s2", null, false, false, null);
        ModelChecker.Report report = ModelChecker.checkModel(
                model(List.of(s1, s2, s3), List.of(t)));
        assertThat(report.reachableStates()).containsExactly("red", "green");
        assertThat(report.unreachableStates()).containsExactly("orphan");
        assertThat(report.warnings()).anyMatch(w -> w.contains("unreachable states: orphan"));
        assertThat(report.status()).isEqualTo("success");
    }

    @Test
    void deadlockDetectionProducesCounterexampleTrace() {
        ModelChecker.McState s1 = new ModelChecker.McState("s1", "start", true, false);
        ModelChecker.McState s2 = new ModelChecker.McState("s2", "stuck", false, false);
        ModelChecker.McTransition t = new ModelChecker.McTransition(
                "t1", "move", "s1", "s2", null, false, false, null);
        ModelChecker.Report report = ModelChecker.checkModel(
                model(List.of(s1, s2), List.of(t)));
        assertThat(report.status()).isEqualTo("failure");
        assertThat(report.deadlockTrace()).isNotNull();
        assertThat(report.deadlockTrace()).containsExactly("start", "stuck");
        assertThat(report.errors()).anyMatch(e -> e.contains("deadlock"));
    }

    @Test
    void missingInitialStateFailsStructuralValidation() {
        ModelChecker.McState s1 = new ModelChecker.McState("s1", "only", false, false);
        ModelChecker.Report report = ModelChecker.checkModel(
                model(List.of(s1), List.of()));
        assertThat(report.status()).isEqualTo("failure");
        assertThat(report.errors()).anyMatch(e -> e.contains("no initial state declared"));
    }

    @Test
    void invariantReferencingUnknownIdentifierFails() {
        ModelChecker.McState s1 = new ModelChecker.McState("s1", "init", true, true);
        ModelChecker.McInvariant inv = new ModelChecker.McInvariant(
                "i1", "inv1", "x > unknownVar", false);
        ModelChecker.McModel m = new ModelChecker.McModel(
                List.of(s1), List.of(), List.of(inv), List.of(), List.of(), List.of("x"), List.of());
        ModelChecker.Report report = ModelChecker.checkModel(m);
        assertThat(report.verdicts()).anySatisfy(v -> {
            assertThat(v.result()).isEqualTo("FAIL");
            assertThat(v.detail()).contains("references undefined identifier(s): unknownVar");
        });
    }

    @Test
    void livenessFailsWithoutTerminalOrCycle() {
        ModelChecker.McState s1 = new ModelChecker.McState("s1", "init", true, false);
        ModelChecker.McProperty prop = new ModelChecker.McProperty(
                "p1", "eventually", "liveness", "init");
        ModelChecker.McModel m = new ModelChecker.McModel(
                List.of(s1), List.of(), List.of(), List.of(prop), List.of(), List.of(), List.of());
        ModelChecker.Report report = ModelChecker.checkModel(m);
        assertThat(report.verdicts()).anySatisfy(v -> {
            assertThat(v.result()).isEqualTo("FAIL");
            assertThat(v.detail()).contains("no reachable terminal and no cycle");
        });
    }

    @Test
    void eventuallyFailsWhenReferencedStateUnreachable() {
        ModelChecker.McState s1 = new ModelChecker.McState("s1", "init", true, true);
        ModelChecker.McState s2 = new ModelChecker.McState("s2", "never", false, false);
        ModelChecker.McTemporalProperty tp = new ModelChecker.McTemporalProperty(
                "t1", "tp1", "<>", "never");
        ModelChecker.McModel m = new ModelChecker.McModel(
                List.of(s1, s2), List.of(), List.of(), List.of(), List.of(tp), List.of(), List.of());
        ModelChecker.Report report = ModelChecker.checkModel(m);
        assertThat(report.verdicts()).anySatisfy(v -> {
            assertThat(v.kind()).isEqualTo("temporal");
            assertThat(v.result()).isEqualTo("FAIL");
            assertThat(v.detail()).contains("NOT reachable");
        });
        assertThat(report.status()).isEqualTo("failure");
    }

    @Test
    void windCompilerRefusesMultipleInitialStates() {
        var s1 = new WindCompiler.RevisionState("s1", "a", true, false);
        var s2 = new WindCompiler.RevisionState("s2", "b", true, false);
        var plan = WindCompiler.buildWindCompilationPlan("rev1",
                List.of(s1, s2), List.of(), List.of(), List.of(), List.of(), List.of());
        assertThat(plan.errors()).anyMatch(e -> e.contains("exactly one initial state"));
    }

    @Test
    void windCompilerDetectsCheckOnlyMismatch() {
        var s1 = new WindCompiler.RevisionState("s1", "a", true, false);
        var task = new WindCompiler.WindTaskRef("task1", "Task One", "tackle-1");
        var mapping = new WindCompiler.WindTaskMapping("s1", "task1", true);
        var plan = WindCompiler.buildWindCompilationPlan("rev1",
                List.of(s1), List.of(), List.of(mapping), List.of(), List.of(task), List.of());
        assertThat(plan.errors()).anyMatch(e -> e.contains("check-only semantics"));
    }

    @Test
    void windCompilerBuildsEdgesWhenBridgeIsComplete() {
        var s1 = new WindCompiler.RevisionState("s1", "start", true, false);
        var s2 = new WindCompiler.RevisionState("s2", "end", false, true);
        var t1 = new WindCompiler.RevisionTransition("tr1", "go", "s1", "s2");
        var task1 = new WindCompiler.WindTaskRef("task1", "Start Task", null);
        var task2 = new WindCompiler.WindTaskRef("task2", "End Task", "tackle-2");
        var tm1 = new WindCompiler.WindTaskMapping("s1", "task1", true);
        var tm2 = new WindCompiler.WindTaskMapping("s2", "task2", false);
        var outcome = new WindCompiler.WindOutcomeRef("out1", "task1", "done");
        var om = new WindCompiler.WindOutcomeMapping("tr1", "task1", "out1");
        var plan = WindCompiler.buildWindCompilationPlan("rev1",
                List.of(s1, s2), List.of(t1), List.of(tm1, tm2), List.of(om),
                List.of(task1, task2), List.of(outcome));
        assertThat(plan.errors()).isEmpty();
        assertThat(plan.nodes()).hasSize(2);
        assertThat(plan.edges()).hasSize(1);
        assertThat(plan.edges().get(0).fromTaskId()).isEqualTo("task1");
        assertThat(plan.edges().get(0).outcomeId()).isEqualTo("out1");
        assertThat(plan.graphDigest()).startsWith("sha256:");
    }

    @Test
    void tlcParserMapsExitCodes() {
        assertThat(TlcParser.exitToStatus(0)).isEqualTo("success");
        assertThat(TlcParser.exitToStatus(11)).isEqualTo("failure");
        assertThat(TlcParser.exitToStatus(12)).isEqualTo("failure");
        assertThat(TlcParser.exitToStatus(150)).isEqualTo("error");
    }

    @Test
    void tlcParserExtractsInvariantViolationAndTrace() {
        String stdout = """
                @!@!@STARTMSG 2110:0 @!@!@
                Invariant Inv1 is violated.
                @!@!@ENDMSG 2110 @!@!@
                @!@!@STARTMSG 2217:1 @!@!@
                <Initial predicate>
                1: x = 0
                @!@!@ENDMSG 2217 @!@!@
                @!@!@STARTMSG 2217:2 @!@!@
                <Next action>
                2: x = 5
                @!@!@ENDMSG 2217 @!@!@
                """;
        TlcParser.ParseOutcome out = TlcParser.parseTlcOutput(stdout, 12);
        assertThat(out.status()).isEqualTo("failure");
        assertThat(out.violated()).isEqualTo("invariant:Inv1");
        assertThat(out.trace()).hasSize(2);
        assertThat(out.trace().get(0)).containsEntry("label", "Initial predicate");
        assertThat(out.trace().get(0).get("state")).isEqualTo(List.of("x = 0"));
        assertThat(out.trace().get(1).get("state")).isEqualTo(List.of("x = 5"));
    }

    @Test
    void tlcParserRecognizesDeadlock() {
        TlcParser.ParseOutcome out = TlcParser.parseTlcOutput("Deadlock reached.\n", 11);
        assertThat(out.status()).isEqualTo("failure");
        assertThat(out.violated()).isEqualTo("deadlock");
    }

    @Test
    void tlcParserSuccessSummary() {
        TlcParser.ParseOutcome out = TlcParser.parseTlcOutput(
                "Model checking completed. No error has been found.\n", 0);
        assertThat(out.status()).isEqualTo("success");
        assertThat(out.summary()).contains("completed:");
    }

    @Test
    void truthfulOutcomeOnlyTlcSuccessVerifies() {
        assertThat(AegisDigest.mapCheckerOutcome("tlc", "success", null).status()).isEqualTo("verified");
        assertThat(AegisDigest.mapCheckerOutcome("tlc", "failure", null).status()).isEqualTo("violated");
        assertThat(AegisDigest.mapCheckerOutcome("structural", "success", null).status()).isEqualTo("unknown");
        assertThat(AegisDigest.mapCheckerOutcome("structural", "failure", null).status()).isEqualTo("invalid");
        assertThat(AegisDigest.mapCheckerOutcome("tlc", "error", null).status()).isEqualTo("unavailable");
    }
}
