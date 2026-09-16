package org.nexus.writequeue.envelope;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * CanonicalEnvelope — the shared Nexus event envelope (mirror of
 * python/nats_envelope/envelope.py CanonicalEnvelope).
 *
 * Serializes to EXACTLY the same JSON shape as the Python {@code to_dict()}
 * so Java and Python interoperate on the NATS bus without a separate
 * conversion layer. Field names are snake_case to match.
 *
 * The write-queue reuses this envelope as transport; the semantic
 * distinction (event vs write-intent) lives in the stream/lifecycle
 * (nexus.write-queue.v1.<target>.<verb>), not in a second envelope format.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class CanonicalEnvelope {

    public String eventId;
    public String eventType;
    public Integer eventVersion;
    public String occurredAt;
    public String originSystem;
    public String originComponent;
    public String domain;
    public Integer ccnfVersion;
    public String epochId;
    public Map<String, String> actor;
    public Map<String, String> intent;
    public String correlationId;
    public String causationId;
    public List<String> sourceEventIds;
    public String executionId;
    public String classification;
    public String policyVersion;
    public String subject;
    public Object payload;

    public static Builder builder() {
        return new Builder();
    }

    /** Serialize to the exact python to_dict() JSON. */
    public String toJson(ObjectMapper mapper) throws Exception {
        ObjectNode n = mapper.createObjectNode();
        n.put("event_id", eventId);
        n.put("event_type", eventType);
        if (eventVersion != null) n.put("event_version", eventVersion);
        n.put("occurred_at", occurredAt);
        n.put("origin_system", originSystem);
        n.put("origin_component", originComponent);
        if (domain != null) n.put("domain", domain);
        if (ccnfVersion != null) n.put("ccnf_version", ccnfVersion);
        if (epochId != null) n.put("epoch_id", epochId);
        if (actor != null) n.set("actor", mapper.valueToTree(actor));
        if (intent != null) n.set("intent", mapper.valueToTree(intent));
        n.put("correlation_id", correlationId);
        if (causationId != null) n.put("causation_id", causationId);
        if (sourceEventIds != null) n.set("source_event_ids", mapper.valueToTree(sourceEventIds));
        if (executionId != null) n.put("execution_id", executionId);
        n.put("classification", classification);
        if (policyVersion != null) n.put("policy_version", policyVersion);
        n.put("subject", subject);
        n.set("payload", mapper.valueToTree(payload));
        return mapper.writeValueAsString(n);
    }

    public static class Builder {
        private final CanonicalEnvelope e = new CanonicalEnvelope();

        public Builder eventType(String v) { e.eventType = v; return this; }
        public Builder originComponent(String v) { e.originComponent = v; return this; }
        public Builder correlationId(String v) { e.correlationId = v; return this; }
        public Builder subject(String v) { e.subject = v; return this; }
        public Builder payload(Object v) { e.payload = v; return this; }
        public Builder eventId(String v) { e.eventId = v; return this; }
        public Builder causationId(String v) { e.causationId = v; return this; }
        public Builder sourceEventIds(List<String> v) { e.sourceEventIds = v; return this; }
        public Builder executionId(String v) { e.executionId = v; return this; }
        public Builder classification(String v) { e.classification = v; return this; }
        public Builder actor(Map<String, String> v) { e.actor = v; return this; }

        public CanonicalEnvelope build() {
            if (e.eventId == null) e.eventId = UUID.randomUUID().toString();
            if (e.eventVersion == null) e.eventVersion = 1;
            if (e.occurredAt == null) e.occurredAt = Instant.now().toString();
            if (e.originSystem == null) e.originSystem = "nexus";
            if (e.classification == null) e.classification = "internal";
            return e;
        }
    }
}