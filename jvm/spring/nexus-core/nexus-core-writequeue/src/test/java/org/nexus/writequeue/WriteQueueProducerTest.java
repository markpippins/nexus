package org.nexus.writequeue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.nexus.writequeue.envelope.CanonicalEnvelope;
import org.nexus.writequeue.model.WriteIntent;

import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Contract-shape tests for the write-queue producer envelope.
 *
 * Verifies the CanonicalEnvelope serializes to the exact python
 * to_dict() shape (snake_case keys) and that the stream subject follows
 * nexus.write-queue.v1.<target>.<verb> per the TypeSpec contract.
 */
class WriteQueueProducerTest {

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void subjectFollowsContract() {
        WriteIntent intent = intent("p-1", "solscript.proposition", "transition-entity");
        assertEquals(
            "nexus.write-queue.v1.solscript.proposition.transition-entity",
            WriteQueueProducer.subjectFor(intent));
    }

    @Test
    void envelopeSerializesToPythonShape() throws Exception {
        WriteIntent intent = intent("tr-abc", "solscript.proposition", "transition-entity");

        CanonicalEnvelope envelope = CanonicalEnvelope.builder()
                .eventType("WriteQueueEntry")
                .originComponent("nexus-core-solscript")
                .correlationId("corr-1")
                .subject(WriteQueueProducer.subjectFor(intent))
                .payload(Map.of("intent", Map.of("writeId", "tr-abc")))
                .classification("internal")
                .build();

        JsonNode node = mapper.readTree(envelope.toJson(mapper));

        // snake_case keys matching python nats_envelope to_dict()
        assertTrue(node.has("event_id"));
        assertTrue(node.has("event_type"));
        assertTrue(node.has("occurred_at"));
        assertTrue(node.has("origin_system"));
        assertTrue(node.has("origin_component"));
        assertTrue(node.has("correlation_id"));
        assertTrue(node.has("classification"));
        assertTrue(node.has("subject"));
        assertTrue(node.has("payload"));
        assertFalse(node.has("eventId"), "must use snake_case event_id, not eventId");

        assertEquals("nexus", node.get("origin_system").asText());
        assertEquals("internal", node.get("classification").asText());
        assertEquals(
            "nexus.write-queue.v1.solscript.proposition.transition-entity",
            node.get("subject").asText());
    }

    @Test
    void envelopeIdempotencyKeyIsWriteId() throws Exception {
        // The payload carries the WriteIntent with writeId = the idempotency key.
        String writeId = "tr-" + UUID.randomUUID();
        WriteIntent intent = intent(writeId, "solscript.proposition", "transition-entity");

        CanonicalEnvelope envelope = CanonicalEnvelope.builder()
                .eventType("WriteQueueEntry")
                .originComponent("nexus-core-solscript")
                .correlationId(writeId)
                .subject(WriteQueueProducer.subjectFor(intent))
                .payload(Map.of("intent", Map.of("writeId", writeId)))
                .classification("internal")
                .build();

        JsonNode node = mapper.readTree(envelope.toJson(mapper));
        assertEquals(writeId, node.get("correlation_id").asText());
        assertEquals(writeId, node.get("payload").get("intent").get("writeId").asText());
    }

    private static WriteIntent intent(String writeId, String target, String verb) {
        return WriteIntent.builder()
                .writeId(writeId)
                .target(target)
                .verb(verb)
                .payload(Map.of("proposition_id", "p-1"))
                .schemaVersion("1")
                .build();
    }
}