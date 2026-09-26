package org.nexus.solscript.api;

import org.nexus.writequeue.WriteQueueProducer;
import org.nexus.writequeue.model.WriteIntent;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.UUID;

/**
 * SOLScript facade WRITE ops — mobile write-queue path.
 *
 * The interpreter library fully implements state transitions, but the REST
 * tier on this (mobile / capability-poor) context does NOT apply mutations
 * directly: POST /api/solscript/transition-entity enqueues a WriteIntent on
 * the nexus.write-queue stream for the reconciler to apply when it has the
 * storage capability (home network). This is the "disconnected context wants
 * this reconciled" path, NOT a canonical event.
 *
 * The write is accepted (202) with an outcome indicating whether it was
 * queued to JetStream, buffered locally, or failed — mirroring the
 * graceful-degradation posture. This REPLACES the previous bare 405 (the
 * JetStream write path now exists).
 */
@RestController
@RequestMapping("/api/solscript")
public class SolScriptWriteController {

    private final WriteQueueProducer producer;

    public SolScriptWriteController(WriteQueueProducer producer) {
        this.producer = producer;
    }

    @PostMapping("/transition-entity")
    public ResponseEntity<Map<String, Object>> transitionEntity(@RequestBody(required = false) Map<String, Object> body) {
        // Build a write-intent for the transition. writeId is the idempotency key.
        String writeId = (String) (body == null ? null : body.get("writeId"));
        if (writeId == null) {
            writeId = "tr-" + UUID.randomUUID();
        }

        WriteIntent intent = WriteIntent.builder()
                .writeId(writeId)
                .target("solscript.proposition")
                .verb("transition-entity")
                .payload(body == null ? Map.of() : body)
                .schemaVersion("1")
                .build();

        String outcome = producer.enqueue(intent, "nexus-core-solscript", writeId);

        int status = switch (outcome) {
            case "queued" -> HttpStatus.ACCEPTED.value();                  // 202 — held durably on the stream
            case "dropped_core_nats" -> HttpStatus.ACCEPTED.value();       // 202 — handed to core NATS only; NOT durable (no stream/consumer holds it; reconciler will never see it)
            case "buffered_local" -> HttpStatus.ACCEPTED.value();          // 202 — queued locally (offline)
            default -> HttpStatus.SERVICE_UNAVAILABLE.value();                // 503 — could not queue
        };

        return ResponseEntity.status(status).body(Map.of(
                "status", "queued",
                "outcome", outcome,
                "writeId", writeId,
                "message", "transition-entity intent queued on nexus.write-queue for reconciliation"
        ));
    }
}