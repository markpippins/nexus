package org.nexus.solscript.models;

import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Deterministic canonical JSON — the Java twin of the TS/JS `stableStringify`
 * (expression-compiler.ts) used for expression cache keys and SHA-256 digests
 * (events.ts). Must produce byte-identical output to:
 *   - sorted keys, no whitespace
 *   - undefined/null -> "null"
 *   - non-finite numbers -> "null"
 *   - arrays/objects rendered without spaces
 * This is the parity-critical serialization; do not substitute Jackson here.
 */
public final class StableJson {
    private StableJson() {}

    public static String stringify(Object value) {
        StringBuilder sb = new StringBuilder();
        append(value, sb);
        return sb.toString();
    }

    private static void append(Object value, StringBuilder sb) {
        if (value == null) {
            sb.append("null");
            return;
        }
        if (value instanceof Number n) {
            double d = n.doubleValue();
            if (!Double.isFinite(d)) { sb.append("null"); return; }
            // JSON.stringify on a number: integral doubles print without decimals.
            if (d == Math.rint(d) && !Double.isInfinite(d)) {
                sb.append((long) d);
            } else {
                sb.append(d);
            }
            return;
        }
        if (value instanceof Boolean b) {
            sb.append(b ? "true" : "false");
            return;
        }
        if (value instanceof String s) {
            appendJsonString(s, sb);
            return;
        }
        if (value instanceof List<?> list) {
            sb.append('[');
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) sb.append(',');
                append(list.get(i), sb);
            }
            sb.append(']');
            return;
        }
        if (value instanceof Map<?, ?> rawMap) {
            // Sorted keys; skip undefined (Java has none, so keep all).
            TreeMap<String, Object> sorted = new TreeMap<>();
            for (Map.Entry<?, ?> e : rawMap.entrySet()) {
                sorted.put(String.valueOf(e.getKey()), e.getValue());
            }
            sb.append('{');
            boolean first = true;
            for (Map.Entry<String, Object> e : sorted.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                appendJsonString(e.getKey(), sb);
                sb.append(':');
                append(e.getValue(), sb);
            }
            sb.append('}');
            return;
        }
        // Fallback for unknown types: stringify via toString (JS String(value)).
        appendJsonString(String.valueOf(value), sb);
    }

    private static void appendJsonString(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\b' -> sb.append("\\b");
                case '\f' -> sb.append("\\f");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
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
}