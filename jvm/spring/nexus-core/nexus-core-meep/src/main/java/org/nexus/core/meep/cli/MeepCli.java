package org.nexus.core.meep.cli;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.InputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Scanner;
import org.nexus.core.meep.MeepPipeline;
import org.nexus.core.meep.execution.Execution;
import org.nexus.core.meep.model.CerLog;
import org.nexus.core.meep.model.Models;

/**
 * CLI deployment profile of the MEEP kernel — Java port of the Python
 * {@code meep.cli} console script (alternate deployment profile; the
 * Python CLI and the Spring adapter remain independent surfaces over the
 * same contracts).
 *
 * Usage:
 *   java -cp nexus-core-meep.jar org.nexus.core.meep.cli.MeepCli [prompt...] [-o FILE] [--replay] [--version]
 *   echo "hello" | java -cp nexus-core-meep.jar org.nexus.core.meep.cli.MeepCli
 *
 * Output: JSON array of CER events with the Python CLI's snake_case field
 * names. (Display formatting is CLI-level; byte parity guarantees live in
 * the kernel's CanonicalJson hashing, not here.)
 */
public final class MeepCli {
    static final String VERSION = "0.1.0";

    private MeepCli() {}

    public static void main(String[] args) {
        System.exit(run(List.of(args), System.in, System.out, System.err));
    }

    /** Testable entrypoint: returns the process exit code. */
    public static int run(List<String> args, InputStream in, PrintStream out, PrintStream err) {
        String output = null;
        boolean replay = false;
        boolean version = false;
        List<String> promptWords = new ArrayList<>();

        for (int i = 0; i < args.size(); i++) {
            String arg = args.get(i);
            switch (arg) {
                case "--replay" -> replay = true;
                case "--version" -> version = true;
                case "-o", "--output" -> {
                    if (i + 1 < args.size()) output = args.get(++i);
                    else { err.println("meep: " + arg + " requires a file path"); return 2; }
                }
                default -> {
                    if (arg.startsWith("--output=")) output = arg.substring(9);
                    else if (arg.startsWith("-o=")) output = arg.substring(3);
                    else promptWords.add(arg);
                }
            }
        }

        if (version) {
            out.println("MEEP v" + VERSION);
            return 0;
        }

        String prompt = String.join(" ", promptWords).trim();
        if (prompt.isEmpty()) {
            Scanner scanner = new Scanner(in, StandardCharsets.UTF_8).useDelimiter("\\A");
            if (scanner.hasNext()) prompt = scanner.next().trim();
        }
        if (prompt.isEmpty()) {
            err.println("No prompt provided. Pipe text or pass as arguments.");
            err.println("Usage: org.nexus.core.meep.cli.MeepCli [prompt...] [-o FILE] [--replay] [--version]");
            return 1;
        }

        CerLog log = MeepPipeline.run(prompt);
        String serialised = serialise(log);

        try {
            if (output != null && !output.isBlank()) {
                Files.writeString(Path.of(output), serialised, StandardCharsets.UTF_8);
                err.println("CER log written to " + output);
                if (replay) err.println("Final state: " + Execution.replay(log));
            } else {
                out.println(serialised);
            }
        } catch (Exception e) {
            err.println("meep: " + e.getMessage());
            return 1;
        }
        return 0;
    }

    static String serialise(CerLog log) {
        ObjectMapper mapper = new ObjectMapper();
        ArrayNode records = mapper.createArrayNode();
        for (Models.CerEvent event : log.events()) {
            ObjectNode node = records.addObject();
            node.put("event_id", event.eventId());
            node.put("timestamp", event.timestamp());
            node.put("execution_id", event.executionId());
            node.put("node_id", event.nodeId());
            node.put("event_type", event.eventType());
            node.set("payload", mapper.valueToTree(event.payload()));
            node.put("prev_event_hash", event.prevEventHash());
        }
        try {
            return mapper.writerWithDefaultPrettyPrinter().writeValueAsString(records);
        } catch (Exception e) {
            throw new IllegalStateException("CER serialisation failed", e);
        }
    }
}
