package org.nexus.solscript.events;

import org.nexus.solscript.models.StableJson;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Keychains event contracts — faithful port of typescript/solscript/src/events.ts.
 * stableDigest MUST produce byte-identical digests to Python's stable_digest
 * (canonical JSON via {@link StableJson}, then SHA-256 hex).
 */
public final class KeychainEvents {

    public static final int KEYCHAIN_EVENT_SCHEMA_VERSION = 1;
    public static final int READ_SET_MANIFEST_SCHEMA_VERSION = 1;

    private KeychainEvents() {}

    public static String stableDigest(Object value) {
        return sha256Hex(StableJson.stringify(value));
    }

    public static String sha256Hex(String input) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(input.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(digest.length * 2);
            for (byte b : digest) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    // ── Read-set manifest ────────────────────────────────────────────

    public static class ReadSetManifest {
        public String sourceNamespace;
        public String evaluationId;
        public String evaluationKind;
        public String targetId;
        public String asOf;
        public String visibilityScope;
        public String evaluatorId;
        public Map<String, Object> permissions = new LinkedHashMap<>();
        public Map<String, Object> context = new LinkedHashMap<>();
        public List<Map<String, Object>> sourceRefs = new ArrayList<>();
        public String manifestId;
        public int schemaVersion;
        public String recordedAt;
        public String manifestDigest;
        public String status;
        public String idempotencyKey;
    }

    public static ReadSetManifest buildReadSetManifest(Map<String, Object> input) {
        ReadSetManifest m = new ReadSetManifest();
        m.sourceNamespace = (String) input.get("sourceNamespace");
        m.evaluationId = (String) input.get("evaluationId");
        m.evaluationKind = (String) input.get("evaluationKind");
        m.targetId = (String) input.get("targetId");
        m.asOf = (String) input.get("asOf");
        m.visibilityScope = input.getOrDefault("visibilityScope", "all").toString();
        m.evaluatorId = input.get("evaluatorId") != null ? input.get("evaluatorId").toString() : null;
        m.permissions = castMap(input.get("permissions"));
        m.context = castMap(input.get("context"));
        m.sourceRefs = castListOfMaps(input.get("sourceRefs"));
        m.manifestId = input.get("manifestId") != null ? input.get("manifestId").toString() : UUID.randomUUID().toString();
        m.schemaVersion = READ_SET_MANIFEST_SCHEMA_VERSION;
        m.recordedAt = input.get("recordedAt") != null ? input.get("recordedAt").toString() : nowIso();
        m.status = input.get("status") != null ? input.get("status").toString() : "pending";

        // Python: as_of retained but EXCLUDED from the digest descriptor.
        Map<String, Object> descriptor = new LinkedHashMap<>();
        descriptor.put("schema_version", m.schemaVersion);
        descriptor.put("source_namespace", m.sourceNamespace);
        descriptor.put("evaluation_id", m.evaluationId);
        descriptor.put("evaluation_kind", m.evaluationKind);
        descriptor.put("target_id", m.targetId);
        descriptor.put("visibility_scope", m.visibilityScope);
        descriptor.put("evaluator_id", m.evaluatorId);
        descriptor.put("permissions", m.permissions);
        descriptor.put("context", m.context);
        descriptor.put("source_refs", m.sourceRefs);
        m.manifestDigest = stableDigest(descriptor);
        m.idempotencyKey = m.sourceNamespace + ":evaluation:" + m.evaluationKind + ":" + m.evaluationId;
        return m;
    }

    public static Map<String, Object> manifestToDict(ReadSetManifest m) {
        Map<String, Object> d = toMap(m);
        d.put("idempotencyKey", m.idempotencyKey);
        return d;
    }

    // ── Keychain events ──────────────────────────────────────────────

    public static class KeychainEvent {
        public String sourceNamespace;
        public String sourceEventId;
        public String kind;
        public String outcome;
        public String aggregateId;
        public int schemaVersion;
        public String causationId;
        public String correlationId;
        public String actor;
        public String contractId;
        public String evaluatorId;
        public String lawId;
        public String effectiveAt;
        public String recordedAt;
        public Map<String, Object> readSet = new LinkedHashMap<>();
        public Map<String, Object> payload = new LinkedHashMap<>();
        public String checkpointStatus;
        public String eventId;
        public String idempotencyKey;
    }

    public static String keychainEventId(String sourceNamespace, String sourceEventId) {
        return sourceNamespace + ":" + sourceEventId;
    }

    public static Map<String, Object> keychainEventToDict(KeychainEvent e) {
        Map<String, Object> d = toMap(e);
        d.put("event_id", e.eventId);
        d.put("idempotency_key", e.idempotencyKey);
        return d;
    }

    private static final Map<String, String> TRANSITION_KINDS = Map.of(
        "committed", "resolution.transition.committed",
        "refused", "resolution.transition.refused",
        "rejected", "resolution.transition.rejected");

    /** buildTransitionEvent — mirrors TS exactly. */
    public static KeychainEvent buildTransitionEvent(Map<String, Object> input) {
        String sourceEventId = (String) input.get("sourceEventId");
        String entityId = (String) input.get("entityId");
        String transitionId = (String) input.get("transitionId");
        String outcome = (String) input.get("outcome");
        Object results = input.get("results");
        Object conceptId = input.get("conceptId");
        Object stateBefore = input.get("stateBefore");
        Object stateAfter = input.get("stateAfter");
        Object effectiveAt = input.get("effectiveAt");
        Object correlationId = input.get("correlationId");
        Object actor = input.get("actor");
        Object sourceNamespace = input.get("sourceNamespace");

        KeychainEvent e = new KeychainEvent();
        e.sourceNamespace = sourceNamespace != null ? sourceNamespace.toString() : "sol-api";
        e.sourceEventId = sourceEventId;
        e.kind = TRANSITION_KINDS.getOrDefault(outcome, "resolution.transition.failed");
        e.outcome = outcome;
        e.aggregateId = entityId;
        e.causationId = sourceEventId;
        e.correlationId = correlationId != null ? correlationId.toString() : sourceEventId;
        e.actor = actor != null ? actor.toString() : "sol-api";
        e.contractId = null;
        e.evaluatorId = null;
        e.lawId = null;
        e.effectiveAt = effectiveAt != null ? effectiveAt.toString() : null;
        e.schemaVersion = KEYCHAIN_EVENT_SCHEMA_VERSION;
        e.recordedAt = nowIso();
        e.checkpointStatus = "pending";

        Map<String, Object> readSet = new LinkedHashMap<>();
        readSet.put("entity_id", entityId);
        readSet.put("transition_id", transitionId);
        readSet.put("concept_id", conceptId);
        readSet.put("guard_results", results);
        readSet.put("state_before", stateBefore);
        readSet.put("state_after", stateAfter);
        e.readSet = readSet;

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("transition_id", transitionId);
        e.payload = payload;

        e.eventId = keychainEventId(e.sourceNamespace, e.sourceEventId);
        e.idempotencyKey = e.eventId;
        return e;
    }

    /** buildEvaluationEvent — mirrors TS exactly. */
    public static KeychainEvent buildEvaluationEvent(Map<String, Object> input) {
        String sourceEventId = (String) input.get("sourceEventId");
        String evaluationKind = (String) input.get("evaluationKind");
        String targetId = (String) input.get("targetId");
        ReadSetManifest manifest = (ReadSetManifest) input.get("manifest");
        Map<String, Object> result = castMap(input.get("result"));
        Object outcome = input.get("outcome");
        Object evaluatorId = input.get("evaluatorId");
        Object actor = input.get("actor");
        Object sourceNamespace = input.get("sourceNamespace");

        KeychainEvent e = new KeychainEvent();
        e.sourceNamespace = sourceNamespace != null ? sourceNamespace.toString() : "sol-api";
        e.sourceEventId = sourceEventId;
        e.kind = "resolution.evaluation." + evaluationKind + ".completed";
        e.outcome = outcome != null ? outcome.toString() : "committed";
        e.aggregateId = targetId;
        e.causationId = manifest.manifestId;
        e.correlationId = manifest.evaluationId;
        e.actor = actor != null ? actor.toString() : "sol-api";
        e.contractId = null;
        e.evaluatorId = evaluatorId != null ? evaluatorId.toString() : "solscript";
        e.lawId = null;
        e.effectiveAt = manifest.asOf;
        e.schemaVersion = KEYCHAIN_EVENT_SCHEMA_VERSION;
        e.recordedAt = nowIso();
        e.checkpointStatus = "pending";

        Map<String, Object> readSet = new LinkedHashMap<>();
        readSet.put("manifest_id", manifest.manifestId);
        readSet.put("manifest_digest", manifest.manifestDigest);
        readSet.put("evaluation_id", manifest.evaluationId);
        readSet.put("source_namespace", manifest.sourceNamespace);
        e.readSet = readSet;

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("evaluation_kind", evaluationKind);
        payload.put("target_id", targetId);
        payload.put("manifest_id", manifest.manifestId);
        payload.put("result", result);
        e.payload = payload;

        e.eventId = keychainEventId(e.sourceNamespace, e.sourceEventId);
        e.idempotencyKey = e.eventId;
        return e;
    }

    // ── Helpers ──────────────────────────────────────────────────────

    private static String nowIso() {
        return java.time.OffsetDateTime.now().toString();
    }

    private static Map<String, Object> toMap(Object o) {
        // Reflectively copy public fields to a LinkedHashMap (JS {...e} spread).
        Map<String, Object> m = new LinkedHashMap<>();
        for (java.lang.reflect.Field f : o.getClass().getDeclaredFields()) {
            try {
                f.setAccessible(true);
                m.put(f.getName(), f.get(o));
            } catch (IllegalAccessException ignored) { }
        }
        return m;
    }

    private static Map<String, Object> castMap(Object o) {
        if (o == null) return new LinkedHashMap<>();
        if (o instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : m.entrySet()) out.put(String.valueOf(e.getKey()), e.getValue());
            return out;
        }
        return new LinkedHashMap<>();
    }

    private static List<Map<String, Object>> castListOfMaps(Object o) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (o instanceof List<?> l) {
            for (Object item : l) out.add(castMap(item));
        }
        return out;
    }
}