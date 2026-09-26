package org.nexus.writequeue;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.nats.client.Connection;
import io.nats.client.JetStream;
import org.nexus.writequeue.envelope.CanonicalEnvelope;
import org.nexus.writequeue.model.WriteIntent;
import org.nexus.writequeue.model.WriteQueueEntry;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

/**
 * WriteQueueProducer — the mobile write-queue producer.
 *
 * Enqueues a reconciliation INTENT onto the nexus.write-queue JetStream
 * stream (subject nexus.write-queue.v1.<target>.<verb>) as a
 * CanonicalEnvelope. This is the "disconnected / capability-poor context
 * wants this reconciled" path — NOT a canonical event (those go on
 * nexus.events after the reconciler applies the intent).
 *
 * Graceful degradation (mirrors python/cascade/nats_publisher.py): if NATS
 * or JetStream is unavailable, the intent is written to a durable local
 * log buffer rather than thrown into the caller. The REST layer must never
 * 5xx because the write-queue is unreachable on a mobile context.
 */
@Component
public class WriteQueueProducer {

    public static final String SUBJECT_PREFIX = "nexus.write-queue.v1";
    private final NatsConfig.NatsConnectionManager nats;
    private final ObjectMapper mapper = new ObjectMapper();

    public WriteQueueProducer(NatsConfig.NatsConnectionManager nats) {
        this.nats = nats;
    }

    /** Build the stream subject for an intent. */
    public static String subjectFor(WriteIntent intent) {
        return SUBJECT_PREFIX + "." + intent.target + "." + intent.verb;
    }

    /**
     * Enqueue a write intent. Returns the enqueue outcome. Never throws on
     * NATS unavailability — falls back to a buffered/logged intent.
     *
     * @param intent the mutation to reconcile
     * @param originComponent which disconnected context queued it
     * @param correlationId idempotency/provenance link
     * @return the publish outcome ("queued" when JetStream accepted,
     *         "dropped_core_nats" when only core NATS was available — the
     *         publish succeeds at the server but NO stream/consumer holds
     *         it, so the intent is not durably held for reconciliation;
     *         "buffered_local" when NATS was unavailable and the intent was
     *         written to the durable local fallback)
     */
    public String enqueue(WriteIntent intent, String originComponent, String correlationId) {
        String subject = subjectFor(intent);

        WriteQueueEntry entry = WriteQueueEntry.builder()
                .intent(intent)
                .correlationId(correlationId != null ? correlationId : UUID.randomUUID().toString())
                .originComponent(originComponent)
                .queuedAt(Instant.now().toString())
                .build();

        CanonicalEnvelope envelope = CanonicalEnvelope.builder()
                .eventType("WriteQueueEntry")
                .originComponent(originComponent)
                .correlationId(entry.correlationId)
                .subject(subject)
                .payload(entry)
                .classification("internal")
                .build();

        Connection conn = nats.connect();
        if (conn == null) {
            return bufferLocally(envelope, subject);
        }

        JetStream js = nats.jetStream();
        try {
            byte[] bytes = envelope.toJson(mapper).getBytes(java.nio.charset.StandardCharsets.UTF_8);
            if (js != null) {
                try {
                    js.publish(subject, bytes);
                    return "queued";
                } catch (Exception jsErr) {
                    // JetStream unavailable — core-NATS publish is the only
                    // remaining transport, but the write-queue reconciler
                    // consumes from the JetStream STREAM, not from a core
                    // subscription, so this intent reaches nothing durable
                    // (vocabulary ruled in f63bfbc7 / Option A: honesty over
                    // optimism). Callers that need durability must provision
                    // the stream (bin/ensure-write-queue-stream.py).
                    conn.publish(subject, bytes);
                    conn.flush(java.time.Duration.ofSeconds(2));
                    return "dropped_core_nats";
                }
            }
            conn.publish(subject, bytes);
            conn.flush(java.time.Duration.ofSeconds(2));
            return "dropped_core_nats";
        } catch (Exception e) {
            return bufferLocally(envelope, subject);
        }
    }

    /** Async variant for callers that don't want to block. */
    public CompletableFuture<String> enqueueAsync(WriteIntent intent, String originComponent, String correlationId) {
        return CompletableFuture.completedFuture(enqueue(intent, originComponent, correlationId));
    }

    /**
     * Durable local fallback when NATS is unreachable. Writes the intent to
     * a JSONL buffer file so it can be drained/replayed when connectivity
     * returns (mobile offline posture). Never throws.
     */
    private String bufferLocally(CanonicalEnvelope envelope, String subject) {
        try {
            String dir = System.getenv().getOrDefault("NEXUS_CORE_WRITEQUEUE_DIR", "/tmp/nexus-writequeue");
            java.nio.file.Files.createDirectories(java.nio.file.Path.of(dir));
            java.nio.file.Files.writeString(
                java.nio.file.Path.of(dir, "write-queue-buffer.jsonl"),
                envelope.toJson(mapper) + "\n",
                java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND);
            return "buffered_local";
        } catch (Exception e) {
            return "buffered_failed";
        }
    }
}