package org.nexus.core.meep.model;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.nexus.core.meep.serialization.CanonicalJson;

/**
 * Append-only hash-chained CER event log (Python reference: meep.models.CERLog).
 * The only mutation is append(); each event's prevEventHash is sealed to the
 * running chain head at insertion time.
 */
public final class CerLog {
    private final List<Models.CerEvent> events = new ArrayList<>();
    private String tailHash = "genesis";

    public void append(Models.CerEvent input) {
        Models.CerEvent event = new Models.CerEvent(input.eventId(), input.timestamp(), input.executionId(),
                input.nodeId(), input.eventType(), Map.copyOf(input.payload()), tailHash);
        tailHash = CanonicalJson.sha256(Map.of("event_id", event.eventId(), "timestamp", event.timestamp(),
                "execution_id", event.executionId(), "node_id", event.nodeId(), "event_type", event.eventType(),
                "payload", event.payload(), "prev_event_hash", event.prevEventHash()));
        events.add(event);
    }

    /** Immutable view of the log. */
    public List<Models.CerEvent> events() { return List.copyOf(events); }
    public String tailHash() { return tailHash; }
    public int size() { return events.size(); }
}
