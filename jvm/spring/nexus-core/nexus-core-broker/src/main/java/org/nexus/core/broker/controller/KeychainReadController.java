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
        return Map.of(
            "enabled", true,
            "latestSnapshot", (Integer) null,
            "latestSnapshotAt", (String) null,
            "entryCount", 0,
            "totalRecordsProjected", (Integer) null,
            "supersededRecords", (Integer) null);
    }

    private static Map<String, Object> readOnlyStatus() {
        return Map.of(
            "enabled", true,
            "intervalMs", 0,
            "latestSnapshot", (Integer) null,
            "latestSnapshotAt", (String) null);
    }
}