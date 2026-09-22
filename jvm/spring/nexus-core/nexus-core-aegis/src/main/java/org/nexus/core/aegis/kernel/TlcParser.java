package org.nexus.core.aegis.kernel;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * TLC (TLA+ model checker) output parser — port of the pure parser half of
 * typescript/aegis-srv/src/tlc-runner.ts.
 *
 * <p>The JVM kernel embeds the parser (unit-testable on captured fixtures);
 * the runner half is a thin process invocation over {@code java -jar
 * tla2tools.jar} provided by {@link TlcRunner}, with the same CLI contract:
 * exit 0 = success, 11 = deadlock, 12 = invariant violation, 150 = parse
 * error. {@code -tool} mode wraps output in
 * {@code @!@!@STARTMSG <code> ... @!@!@ENDMSG} blocks; counterexample states
 * are msg 2217 blocks, "Invariant X is violated" is msg 2110.
 */
public final class TlcParser {

    private TlcParser() {}

    public record ParseOutcome(String status, String violated, List<Map<String, Object>> trace,
                               String summary) {}

    /** Map a TLC exit code to a coarse status. */
    public static String exitToStatus(int code) {
        if (code == 0) return "success";
        if (code == 11 || code == 12) return "failure";
        return "error"; // includes 150 (parse error), 1, timeout, etc.
    }

    private static final Pattern INV_VIOLATED =
            Pattern.compile("Invariant\\s+([^\\s.]+)\\s+is violated", Pattern.CASE_INSENSITIVE);
    private static final Pattern PROP_VIOLATED =
            Pattern.compile("Property\\s+([^\\s.]+)\\s+is violated", Pattern.CASE_INSENSITIVE);
    private static final Pattern DEADLOCK = Pattern.compile("Deadlock reached");
    private static final Pattern TOOL_BLOCK = Pattern.compile(
            "@!@!@STARTMSG\\s+2217[:\\d]*\\s*@!@!@\\s*([\\s\\S]*?)@!@!@ENDMSG\\s+2217");
    private static final Pattern ACTION_LABEL = Pattern.compile("<([^>]*)>");
    private static final Pattern STEP_PREFIX = Pattern.compile("^\\d+\\s*:\\s*");
    private static final Pattern COMPLETED = Pattern.compile("Model checking completed\\.([^\\n]*)");
    private static final Pattern FINISHED_AT = Pattern.compile("Finished in \\d+ms at \\(([^)]+)\\)");

    /**
     * Parse TLC {@code -tool} stdout + exit code into a structured outcome.
     * Pure and deterministic — unit-testable with captured fixtures.
     */
    public static ParseOutcome parseTlcOutput(String stdout, int exitCode) {
        String violated = null;
        List<Map<String, Object>> trace = null;
        String status = exitToStatus(exitCode);

        Matcher invViol = INV_VIOLATED.matcher(stdout);
        Matcher propViol = PROP_VIOLATED.matcher(stdout);
        if (invViol.find()) {
            violated = "invariant:" + invViol.group(1);
        } else if (propViol.find()) {
            violated = "property:" + propViol.group(1);
        } else if (DEADLOCK.matcher(stdout).find()) {
            violated = "deadlock";
        }

        if ("failure".equals(status)) {
            List<Map<String, Object>> steps = new ArrayList<>();
            Matcher block = TOOL_BLOCK.matcher(stdout);
            while (block.find()) {
                String body = block.group(1).trim();
                Matcher actionMatch = ACTION_LABEL.matcher(body);
                String label = actionMatch.find() ? actionMatch.group(1) : "";
                List<String> assignments = new ArrayList<>();
                for (String line : body.split("\n")) {
                    String l = STEP_PREFIX.matcher(line.trim()).replaceFirst("");
                    l = l.trim();
                    if (!l.isEmpty() && !l.startsWith("@!@!@") && !l.startsWith("<")) {
                        assignments.add(l);
                    }
                }
                Map<String, Object> step = new LinkedHashMap<>();
                step.put("label", label.isEmpty() ? "state" : label);
                step.put("state", assignments);
                steps.add(step);
            }
            if (steps.isEmpty()) {
                // Fallback: non-tool mode — capture the whole behavior region.
                Matcher bm = Pattern.compile("The behavior up to this point is:\\s*([\\s\\S]*)")
                        .matcher(stdout);
                if (bm.find()) {
                    Map<String, Object> raw = new LinkedHashMap<>();
                    raw.put("raw", bm.group(1).trim());
                    trace = List.of(raw);
                }
            } else {
                trace = steps;
            }
        }

        String summary = null;
        Matcher completed = COMPLETED.matcher(stdout);
        Matcher finishedAt = FINISHED_AT.matcher(stdout);
        if (completed.find()) {
            summary = "completed:" + completed.group(1).trim();
        } else if (finishedAt.find()) {
            summary = "finished at " + finishedAt.group(1);
        }

        return new ParseOutcome(status, violated, trace, summary);
    }
}
