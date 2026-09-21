package org.nexus.core.aegis.kernel;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Deterministic digests for content-addressed artifacts — port of
 * typescript/aegis-srv/src/phase-a.ts.
 *
 * <p>{@link #canonicalJson(Object)} serializes with object keys sorted
 * recursively; array order is preserved because array order is part of a
 * model/result contract. {@link #digestJson(Object)} prefixes the SHA-256
 * hex digest with {@code sha256:} — byte-compatible with the TS service.
 *
 * <p>{@link #mapCheckerOutcome} maps (engine, status) to the truthful
 * outcome vocabulary: only real TLC success can establish a verified safety
 * result; the structural checker is deliberately evidence-producing but not
 * a formal proof, so its success remains {@code unknown}.
 */
public final class AegisDigest {

    private AegisDigest() {}

    /** Canonical JSON: recursive key sort, JSON string escaping, JS-compatible number rendering. */
    public static String canonicalJson(Object value) {
        StringBuilder sb = new StringBuilder();
        writeValue(sb, value);
        return sb.toString();
    }

    private static void writeValue(StringBuilder sb, Object value) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof String s) {
            writeString(sb, s);
        } else if (value instanceof Boolean b) {
            sb.append(b ? "true" : "false");
        } else if (value instanceof Double d) {
            writeDouble(sb, d);
        } else if (value instanceof Float f) {
            writeDouble(sb, f.doubleValue());
        } else if (value instanceof Number n) {
            sb.append(n.toString());
        } else if (value instanceof Map<?, ?> map) {
            TreeMap<String, Object> sorted = new TreeMap<>();
            for (Map.Entry<?, ?> e : map.entrySet()) {
                sorted.put(String.valueOf(e.getKey()), e.getValue());
            }
            sb.append('{');
            boolean first = true;
            for (Map.Entry<String, Object> e : sorted.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                writeString(sb, e.getKey());
                sb.append(':');
                writeValue(sb, e.getValue());
            }
            sb.append('}');
        } else if (value instanceof List<?> list) {
            sb.append('[');
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) sb.append(',');
                writeValue(sb, list.get(i));
            }
            sb.append(']');
        } else {
            writeString(sb, value.toString());
        }
    }

    /** Double rendering matching JavaScript Number.prototype.toString for finite values. */
    private static void writeDouble(StringBuilder sb, double d) {
        if (Double.isNaN(d) || Double.isInfinite(d)) {
            sb.append("null"); // JSON.stringify semantics
            return;
        }
        if (d == Math.rint(d) && Math.abs(d) < 1e21) {
            sb.append((long) d);
        } else {
            sb.append(d);
        }
    }

    /** JSON string escaping matching JSON.stringify (quote, backslash, control chars). */
    private static void writeString(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                case '\b' -> sb.append("\\b");
                case '\f' -> sb.append("\\f");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        sb.append('"');
    }

    /** {@code sha256:<hex>} of the UTF-8 canonical JSON of {@code value}. */
    public static String digestJson(Object value) {
        return sha256Digest(canonicalJson(value));
    }

    public static String sha256Digest(String value) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] hash = md.digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(hash.length * 2);
            for (byte b : hash) {
                hex.append(String.format("%02x", b));
            }
            return "sha256:" + hex;
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    // ── Truthful outcome mapping ─────────────────────────────────────────

    public record TruthfulOutcome(String status, String safetyStatus, String livenessStatus,
                                  String authorityLevel, String reason) {}

    /** Map a (engine, status) pair to the truthful-outcome vocabulary (phase-a.ts). */
    public static TruthfulOutcome mapCheckerOutcome(String engine, String status, String reason) {
        boolean tlc = "tlc".equals(engine);
        boolean success = "success".equals(status);
        boolean failure = "failure".equals(status);

        if (tlc && success) {
            return new TruthfulOutcome("verified", "verified", "unknown", "advisory",
                    orDefault(reason, "TLC completed without a safety violation; liveness remains unknown at the gate"));
        }
        if (tlc && failure) {
            return new TruthfulOutcome("violated", "violated", "unknown", "advisory",
                    orDefault(reason, "TLC found a counterexample or deadlock"));
        }
        if (!tlc && failure) {
            return new TruthfulOutcome("invalid", "invalid", "unknown", "advisory",
                    orDefault(reason, "Structured model failed validation; no formal verification was established"));
        }
        if (!tlc) {
            return new TruthfulOutcome("unknown", "unknown", "unknown", "advisory",
                    orDefault(reason, "Structural analysis completed; it is not a formal proof"));
        }
        return new TruthfulOutcome("unavailable", "unavailable", "unknown", "advisory",
                orDefault(reason, "Formal checker was unavailable or failed to produce a result"));
    }

    private static String orDefault(String reason, String fallback) {
        return reason == null || reason.isBlank() ? fallback : reason;
    }

    /** Convenience: build a JSON-shaped map for result material (sorted by canonicalJson anyway). */
    public static Map<String, Object> obj(Object... pairs) {
        Map<String, Object> m = new TreeMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            m.put((String) pairs[i], pairs[i + 1]);
        }
        return m;
    }

    /** Convenience: list builder for JSON-shaped arrays. */
    public static List<Object> arr(Object... items) {
        return new ArrayList<>(List.of(items));
    }
}
