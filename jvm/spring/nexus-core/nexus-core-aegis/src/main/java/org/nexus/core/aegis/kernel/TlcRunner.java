package org.nexus.core.aegis.kernel;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * TLC runner — port of the process half of typescript/aegis-srv/src/tlc-runner.ts.
 *
 * <p>Stages {@code <module>.tla} + {@code <module>.cfg} into a temp dir and runs
 * {@code java -jar tla2tools.jar -tool -nowarning -terse -config X.cfg -metadir <tmp> X.tla}.
 * Exit codes: 0 success, 11 deadlock, 12 invariant violation, 150 parse error.
 * Output parsing is delegated to {@link TlcParser}. Failure-isolated: any crash
 * or timeout yields an error-status result; {@link #run} returns null only when
 * the tla2tools.jar artifact is absent from the deployment profile.
 */
public final class TlcRunner {

    /** Env var pointing at the bundled tla2tools.jar for this deployment profile. */
    private static final String TLC_JAR_ENV = "AEGIS_TLC_JAR";

    private TlcRunner() {}

    public record TlcResult(String status, String violated,
                            List<Object> trace, String summary, long timingMs) {}

    /**
     * Run TLC over a TLA+ module source. Returns null when the jar is not
     * available in this deployment profile (caller maps that to an
     * engine-unavailable outcome, mirroring the TS unavailable branch).
     */
    public static TlcResult run(String moduleName, String tlaSource,
                                List<String> invariants, List<String> properties,
                                long timeoutMs) throws IOException, InterruptedException {
        String jar = locateJar();
        if (jar == null) return null;

        Path specDir = Files.createTempDirectory("aegis-tlc-");
        try {
            Path tla = specDir.resolve(moduleName + ".tla");
            Files.writeString(tla, tlaSource, StandardCharsets.UTF_8);

            StringBuilder cfg = new StringBuilder();
            if (!invariants.isEmpty()) {
                cfg.append("INVARIANT\n");
                for (String inv : invariants) cfg.append(inv).append('\n');
            }
            if (!properties.isEmpty()) {
                cfg.append("PROPERTY\n");
                for (String prop : properties) cfg.append(prop).append('\n');
            }
            Files.writeString(specDir.resolve(moduleName + ".cfg"), cfg.toString(), StandardCharsets.UTF_8);

            long started = System.currentTimeMillis();
            List<String> cmd = new ArrayList<>(List.of(
                    "java", "-jar", jar, "-tool", "-nowarning", "-terse",
                    "-config", moduleName + ".cfg",
                    "-metadir", specDir.resolve("metadir").toString(),
                    moduleName));
            Process proc = new ProcessBuilder(cmd)
                    .directory(specDir.toFile())
                    .redirectErrorStream(true)
                    .start();
            String stdout = new String(proc.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            boolean finished = proc.waitFor(timeoutMs, TimeUnit.MILLISECONDS);
            if (!finished) {
                proc.destroyForcibly();
                return new TlcResult("error", null, null, "timeout after " + timeoutMs + "ms",
                        System.currentTimeMillis() - started);
            }
            TlcParser.ParseOutcome parsed = TlcParser.parseTlcOutput(stdout, proc.exitValue());
            List<Object> trace = parsed.trace() == null ? null : new ArrayList<>(parsed.trace());
            return new TlcResult(parsed.status(), parsed.violated(), trace, parsed.summary(),
                    System.currentTimeMillis() - started);
        } finally {
            deleteRecursively(specDir);
        }
    }

    private static String locateJar() {
        String env = System.getenv(TLC_JAR_ENV);
        if (env != null && Files.isRegularFile(Path.of(env))) return env;
        for (String candidate : new String[] {
                "tla/tla2tools.jar",
                "../tla/tla2tools.jar",
                "../../../tla/tla2tools.jar",
        }) {
            Path p = Path.of(candidate).toAbsolutePath().normalize();
            if (Files.isRegularFile(p)) return p.toString();
        }
        return null;
    }

    private static void deleteRecursively(Path dir) {
        try (var walk = Files.walk(dir)) {
            walk.sorted(java.util.Comparator.reverseOrder()).forEach(p -> {
                try { Files.deleteIfExists(p); } catch (IOException ignored) { }
            });
        } catch (IOException ignored) { }
    }
}
