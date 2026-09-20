package org.nexus.core.wrp.identity;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import org.nexus.core.wrp.WrpException;

/**
 * CCNF entity-key derivation — Java mirror of python/nexus_core/wrp/identity.py,
 * which is itself a byte-identical mirror of the Go reference
 * (go/wrp/ccnf-ref: serializer.go CanonicalJSON, intents.go NormalizeIntent,
 * identity.go DeriveIdentity). This JVM port is the third runtime producing
 * the same bytes; conformance authority remains with the Go binary + Rust
 * verifier (wr-conf-008/010), now with Python-golden-vector parity guarding
 * this port (WrpGoldenParityTest).
 *
 * CanonicalJSON rules (serializer.go): compact separators (no spaces),
 * sorted map keys, strings escape backslash, quote, \n, \r, \t characters and
 * emit backslash-u00XX for control chars below 0x20,
 * floats in Go strconv 'f' shortest-fixed form, unknown types → null.
 */
public final class CcnfIdentity {
    private static final Set<String> CONTROLLED_VOCAB = Set.of("create", "update", "delete", "execute", "validate", "emit");
    private static final String HEX = "0123456789abcdef";
    private CcnfIdentity() {}

    // ---- CanonicalJSON (byte-identical to Go) ---------------------------

    public static String canonicalJson(Object value) {
        return encode(value);
    }

    private static String encode(Object value) {
        if (value == null) return "null";
        if (value instanceof Boolean b) return b ? "true" : "false";
        if (value instanceof Integer || value instanceof Long) return value.toString();
        if (value instanceof Float || value instanceof Double) return encodeFloat(((Number) value).doubleValue());
        if (value instanceof String s) return encodeString(s);
        if (value instanceof List<?> list) {
            List<String> parts = new ArrayList<>();
            for (Object item : list) parts.add(encode(item));
            return "[" + String.join(",", parts) + "]";
        }
        if (value instanceof Map<?, ?> map) {
            TreeMap<String, Object> sorted = new TreeMap<>();
            map.forEach((k, v) -> sorted.put(String.valueOf(k), v));
            List<String> parts = new ArrayList<>();
            for (Map.Entry<String, Object> e : sorted.entrySet()) {
                parts.add(encodeString(e.getKey()) + ":" + encode(e.getValue()));
            }
            return "{" + String.join(",", parts) + "}";
        }
        return "null";
    }

    /** Go strconv.FormatFloat(f, 'f', -1, 64) shortest fixed-point. */
    static String encodeFloat(double f) {
        if (f == Math.rint(f) && !Double.isInfinite(f) && f >= -9.007199254740992E15 && f <= 9.007199254740992E15) {
            return Long.toString((long) f);
        }
        String s = shortestFixed(f);
        return s;
    }

    private static String shortestFixed(double f) {
        // Java's Double.toString may emit exponents; expand to fixed notation,
        // then trim to Go-style shortest fixed representation.
        if (Double.isNaN(f) || Double.isInfinite(f)) return "null";
        String s = new java.math.BigDecimal(f).toPlainString();
        if (s.contains(".") && !s.contains("E")) {
            s = s.replaceAll("0+$", "").replaceAll("\\.$", "");
            if (s.isEmpty() || s.equals("-")) s = "0";
        }
        return s;
    }

    private static String encodeString(String s) {
        StringBuilder out = new StringBuilder(s.length() + 2);
        out.append('"');
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            switch (ch) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");                    default -> {
                        if (ch < 0x20) {
                            out.append("\\u00");
                            out.append(HEX.charAt((ch >> 4) & 0xF));
                            out.append(HEX.charAt(ch & 0xF));
                        } else {
                            out.append(ch);
                        }
                    }
            }
        }
        out.append('"');
        return out.toString();
    }

    // ---- NormalizeIntent (intents.go mirror) ----------------------------

    @SuppressWarnings("unchecked")
    public static Map<String, Object> normalizeIntent(Object intent) {
        if (!(intent instanceof Map)) {
            if (intent instanceof String s) {
                throw new WrpException("cannot normalize intent: free-text intent '" + s + "' cannot be mapped");
            }
            throw new WrpException("cannot normalize intent: unexpected intent type");
        }
        Map<String, Object> m = (Map<String, Object>) intent;
        Object rawAction = m.get("action");
        if (!(rawAction instanceof String action) || action.isEmpty()) {
            throw new WrpException("cannot normalize intent: empty action in intent");
        }
        if (!CONTROLLED_VOCAB.contains(action)) {
            throw new WrpException("cannot normalize intent: unknown action '" + action + "'");
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("type", "normalized_verb");
        out.put("action", action);
        out.put("target_type", m.getOrDefault("target_type", ""));
        out.put("target_id", m.getOrDefault("target_id", ""));
        return out;
    }

    // ---- DeriveIdentity (identity.go mirror) ----------------------------

    public static String domainToScope(String domain) {
        if ("execution".equals(domain)) return "executiongraph.v2";
        if ("specification".equals(domain)) return "specification.v1";
        if ("system".equals(domain)) return "system.v1";
        return domain + ".v1";
    }

    @SuppressWarnings("unchecked")
    public static Identity deriveIdentity(Map<String, Object> doc) {
        Object rawDomain = doc.getOrDefault("domain", "");
        String domain = rawDomain instanceof String s ? s : "";
        String scope = domainToScope(domain);
        Object rawIntent = doc.get("intent");
        Map<String, Object> intent = rawIntent instanceof Map ? (Map<String, Object>) rawIntent : Map.of();
        if (intent.get("action") == null || String.valueOf(intent.get("action")).isEmpty()) {
            throw new WrpException("cannot derive identity: no action in intent");
        }
        Object rawActor = doc.get("actor");
        Map<String, Object> actor = rawActor instanceof Map ? (Map<String, Object>) rawActor : null;

        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("domain", domain);
        fields.put("intent", intent);
        fields.put("actor", actor);
        fields.put("scope", scope);
        return new Identity(hashEntitySignature(fields), "event", scope);
    }

    public record Identity(String entityKey, String eventType, String scope) {}

    /** Emit path: normalize intent then derive — matches the Go binary's process pipeline. */
    @SuppressWarnings("unchecked")
    public static Identity emitIdentity(Map<String, Object> doc) {
        Map<String, Object> copy = new LinkedHashMap<>(doc);
        copy.put("intent", normalizeIntent(copy.get("intent")));
        return deriveIdentity(copy);
    }

    public static String deriveEntityKey(Map<String, Object> doc) {
        return emitIdentity(doc).entityKey();
    }

    static String hashEntitySignature(Map<String, Object> fields) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            for (String key : new TreeMap<>(fields).keySet()) {
                md.update(key.getBytes(StandardCharsets.UTF_8));
                md.update((byte) 0);
                md.update(canonicalJson(fields.get(key)).getBytes(StandardCharsets.UTF_8));
                md.update((byte) 0);
            }
            StringBuilder out = new StringBuilder(64);
            for (byte b : md.digest()) out.append(String.format("%02x", b));
            return out.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    // ---- canonical WR CCNF input shapes ---------------------------------

    /** Fixed-shape doc for tests: the wr-0001 example from the Python module docstring. */
    public static Map<String, Object> wrnDocFixed() {
        return ccnfInputFromIntentString("", "wr-0001");
    }

    public static Map<String, Object> wrCcnfInput(String wrId, String agentId) {
        Map<String, Object> doc = new LinkedHashMap<>();
        doc.put("event_id", wrId);
        doc.put("actor", Map.of("type", "system", "id", agentId != null ? agentId : "conduit"));
        Map<String, Object> intent = new LinkedHashMap<>();
        intent.put("action", "execute");
        intent.put("target_type", "workrequest");
        intent.put("target_id", "workrequest:" + wrId);
        doc.put("intent", intent);
        doc.put("domain", "execution");
        return doc;
    }

    public static Map<String, Object> ccnfInputFromIntentString(String intent, String wrId) {
        Map<String, Object> doc = wrCcnfInput(wrId, "conduit");
        if (intent != null && !intent.isEmpty()) doc.put("payload", Map.of("intent_source", intent));
        return doc;
    }
}
