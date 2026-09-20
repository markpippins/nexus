package org.nexus.core.meep.serialization;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Canonical JSON serialization matching the Python reference
 * {@code json.dumps(value, sort_keys=True)} byte-for-byte:
 * keys sorted, separators "{k: v, k: v}" / "[a, b]", UTF-8.
 *
 * This is the parity-critical path: ExecutionGraph.contentHash and the
 * CER hash chain are only comparable across runtimes if the byte stream
 * matches Python exactly.
 */
public final class CanonicalJson {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private CanonicalJson() {}

    public static String write(Object value) {
        try { return render(MAPPER.valueToTree(value)); }
        catch (IllegalArgumentException e) { throw new IllegalArgumentException("Cannot canonicalize JSON", e); }
    }

    private static String render(JsonNode node) {
        if (node == null || node.isNull()) return "null";
        if (node.isTextual()) {
            try { return escapeAscii(MAPPER.writeValueAsString(node.textValue())); }
            catch (JsonProcessingException e) { throw new IllegalArgumentException(e); }
        }
        if (node.isNumber() || node.isBoolean()) return node.toString();
        if (node.isArray()) {
            List<String> values = new ArrayList<>();
            for (JsonNode child : (ArrayNode) node) values.add(render(child));
            return "[" + String.join(", ", values) + "]";
        }
        if (node.isObject()) {
            List<String> names = new ArrayList<>();
            node.fieldNames().forEachRemaining(names::add);
            Collections.sort(names);
            List<String> fields = new ArrayList<>();
            for (String name : names) fields.add(render(MAPPER.valueToTree(name)) + ": " + render(node.get(name)));
            return "{" + String.join(", ", fields) + "}";
        }
        return node.toString();
    }

    /**
     * Python json.dumps default ensure_ascii=True: non-ASCII characters are
     * emitted as backslash-uXXXX escapes (surrogate pairs for astral planes). Applied
     * after JSON quoting so the quote characters themselves are untouched.
     */
    static String escapeAscii(String quoted) {
        StringBuilder out = new StringBuilder(quoted.length() + 16);
        quoted.codePoints().forEach(cp -> {
            if (cp < 0x80) {
                out.appendCodePoint(cp);
            } else if (cp > 0xFFFF) {
                cp -= 0x10000;
                int hi = 0xD800 + (cp >> 10), lo = 0xDC00 + (cp & 0x3FF);
                out.append(String.format("\\u%04x\\u%04x", hi, lo));
            } else {
                out.append(String.format("\\u%04x", cp));
            }
        });
        return out.toString();
    }

    public static String sha256(Object value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(write(value).getBytes(StandardCharsets.UTF_8));
            StringBuilder out = new StringBuilder(64);
            for (byte b : digest) out.append(String.format("%02x", b));
            return out.toString();
        } catch (NoSuchAlgorithmException e) { throw new IllegalStateException("SHA-256 unavailable", e); }
    }
}
