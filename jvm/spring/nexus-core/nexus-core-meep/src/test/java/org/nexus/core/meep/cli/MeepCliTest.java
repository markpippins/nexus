package org.nexus.core.meep.cli;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;

/** CLI deployment-profile tests (mirrors python/meep/tests CLI acceptance criteria). */
class MeepCliTest {

    private record Result(int code, String out, String err) {}

    private static Result run(String... args) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ByteArrayOutputStream err = new ByteArrayOutputStream();
        int code = MeepCli.run(List.of(args), new ByteArrayInputStream(new byte[0]),
                new PrintStream(out, true, StandardCharsets.UTF_8), new PrintStream(err, true, StandardCharsets.UTF_8));
        return new Result(code, out.toString(StandardCharsets.UTF_8), err.toString(StandardCharsets.UTF_8));
    }

    @Test
    void positionalPromptProducesJsonEventArray() {
        Result result = run("fix", "the", "bug");
        assertEquals(0, result.code());
        String trimmed = result.out().trim();
        assertTrue(trimmed.startsWith("["), "output should be a JSON array");
        assertTrue(trimmed.contains("\"event_id\""));
        assertTrue(trimmed.contains("\"event_type\""));
        assertTrue(trimmed.contains("\"prev_event_hash\""));
        assertTrue(trimmed.contains("\"node_id\""));
    }

    @Test
    void stdinPromptIsAccepted() {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ByteArrayOutputStream err = new ByteArrayOutputStream();
        int code = MeepCli.run(List.of(), new ByteArrayInputStream("build a service".getBytes(StandardCharsets.UTF_8)),
                new PrintStream(out, true, StandardCharsets.UTF_8), new PrintStream(err, true, StandardCharsets.UTF_8));
        assertEquals(0, code);
        assertTrue(out.toString(StandardCharsets.UTF_8).trim().startsWith("["));
    }

    @Test
    void emptyPromptFailsWithUsage() {
        Result result = run();
        assertEquals(1, result.code());
        assertTrue(result.err().contains("No prompt provided"));
        assertTrue(result.err().contains("Usage:"));
    }

    @Test
    void versionFlagPrintsAndExits() {
        Result result = run("--version");
        assertEquals(0, result.code());
        assertEquals("MEEP v0.1.0", result.out().trim());
    }

    @Test
    void outputFlagWritesFileAndReplayPrintsState() throws Exception {
        java.nio.file.Path cer = java.nio.file.Path.of(java.nio.file.Files.createTempDirectory("meep-cli").toString(), "log.json");
        Result result = run("audit", "compliance", "--output", cer.toString(), "--replay");
        assertEquals(0, result.code());
        assertTrue(result.err().contains("CER log written to " + cer));
        assertTrue(result.err().contains("Final state:"));
        String content = java.nio.file.Files.readString(cer);
        assertTrue(content.trim().startsWith("["));
        assertTrue(content.contains("\"event_type\""));
    }

    @Test
    void outputFlagWithoutPathIsAnArgumentError() {
        Result result = run("prompt", "--output");
        assertEquals(2, result.code());
        assertTrue(result.err().contains("requires a file path"));
    }

    @Test
    void everyPromptYieldsReplayableChain() {
        for (String[] prompt : List.of(new String[]{"build", "a", "service"}, new String[]{"why", "did", "this", "happen"})) {
            Result result = run(prompt);
            assertEquals(0, result.code());
            assertTrue(result.out().contains("NODE_START"));
            assertTrue(result.out().contains("NODE_COMPLETE"));
        }
    }
}
