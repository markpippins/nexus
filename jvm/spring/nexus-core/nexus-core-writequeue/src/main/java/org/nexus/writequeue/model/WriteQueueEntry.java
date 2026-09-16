package org.nexus.writequeue.model;

import java.util.Map;

/**
 * WriteQueueEntry — the payload carried inside a CanonicalEnvelope on the
 * nexus.write-queue stream (typespec/v1/write-queue/models.tsp
 * WriteQueueEntry). The reconciler drains the stream, classifies, and emits
 * canonical events on nexus.events.
 */
public class WriteQueueEntry {
    public WriteIntent intent;
    public String correlationId;
    public String originComponent;
    public String queuedAt;
    public Map<String, Object> result;

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private final WriteQueueEntry e = new WriteQueueEntry();

        public Builder intent(WriteIntent v) { e.intent = v; return this; }
        public Builder correlationId(String v) { e.correlationId = v; return this; }
        public Builder originComponent(String v) { e.originComponent = v; return this; }
        public Builder queuedAt(String v) { e.queuedAt = v; return this; }
        public Builder result(Map<String, Object> v) { e.result = v; return this; }

        public WriteQueueEntry build() { return e; }
    }
}