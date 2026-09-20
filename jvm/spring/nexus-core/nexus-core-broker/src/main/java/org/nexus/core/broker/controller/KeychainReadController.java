package org.nexus.core.broker.controller;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Read-only projection of the `nexus-broker` keychain read ops.
 *
 * The keychain projection is computed from nebula.* / resolution.* history
 * tables by the live broker's keychain-snapshot service. In the local
 * read-only projection that computation is not replicated: the status reads
 * report the read-only posture explicitly (latestSnapshot null, enabled true)
 * rather than fabricate a projection against tables this module does not own.
 * The status shape mirrors the event-driven Moleculer contract: the JVM is
 * only a read-only mirror and therefore reports null timestamps and no chain
 * health rather than inventing values.
 * The trigger (POST) returns 405 — see KeychainWriteController.
 */
@RestController
@RequestMapping("/api/keychain-snapshot")
public class KeychainReadController {

    @GetMapping("/status")
    public Map<String, Object> status() {
        return readOnlyStatus();
    }

    @GetMapping("/agent-records/status")
    public Map<String, Object> agentRecordsStatus() {
        var m = new java.util.LinkedHashMap<String, Object>();
        m.put("enabled", true);
        m.put("latestSnapshot", null);
        m.put("latestSnapshotAt", null);
        m.put("entryCount", 0);
        m.put("totalRecordsProjected", null);
        m.put("supersededRecords", null);
        return m;
    }

    private static Map<String, Object> readOnlyStatus() {
        var m = new java.util.LinkedHashMap<String, Object>();
        m.put("enabled", true);
        m.put("mode", "event_driven");
        m.put("outboxPollMs", 0);
        m.put("lastEventAt", null);
        m.put("lastSnapshotAt", null);
        m.put("latestSnapshot", null);
        m.put("latestSnapshotAt", null);
        m.put("chainHealth", null);
        m.put("deepestRewindSinceBoot", 0);
        return m;
    }
}