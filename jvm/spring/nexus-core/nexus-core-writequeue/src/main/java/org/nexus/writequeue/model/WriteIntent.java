package org.nexus.writequeue.model;

import java.util.Map;

/**
 * WriteIntent — the mutation a disconnected / capability-poor context WANTS
 * reconciled (typespec/v1/write-queue/models.tsp WriteIntent).
 *
 * An INTENT, not an event: nothing here has happened yet. `writeId` is the
 * idempotency key; `baseVersion` lets the reconciler detect staleness;
 * `requiredCapability` lets it detect missing capability / authorization
 * change.
 */
public class WriteIntent {
    public String writeId;
    public String target;
    public String verb;
    public Map<String, Object> payload;
    public String schemaVersion;
    public String baseVersion;
    public String requiredCapability;
    public Map<String, String> actor;

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private final WriteIntent i = new WriteIntent();

        public Builder writeId(String v) { i.writeId = v; return this; }
        public Builder target(String v) { i.target = v; return this; }
        public Builder verb(String v) { i.verb = v; return this; }
        public Builder payload(Map<String, Object> v) { i.payload = v; return this; }
        public Builder schemaVersion(String v) { i.schemaVersion = v; return this; }
        public Builder baseVersion(String v) { i.baseVersion = v; return this; }
        public Builder requiredCapability(String v) { i.requiredCapability = v; return this; }
        public Builder actor(Map<String, String> v) { i.actor = v; return this; }

        public WriteIntent build() { return i; }
    }
}