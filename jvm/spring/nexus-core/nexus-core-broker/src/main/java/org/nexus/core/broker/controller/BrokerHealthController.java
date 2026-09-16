package org.nexus.core.broker.controller;

import java.time.OffsetDateTime;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Read-only projection of `nexus-broker` GET /api/health and GET /api/workers.
 *
 * In the local read-only projection the worker tier is NOT running (it spawns
 * processes / talks to live DB); the introspection reports the declared tier
 * with status "not-declared" to reflect that the live workers are absent,
 * matching the source's statusFor() semantics.
 */
@RestController
@RequestMapping("/api")
public class BrokerHealthController {

    @GetMapping("/health")
    public Health health() {
        return new Health("ok", "nexus-broker", "nexus",
            List.of("worker.harness", "worker.pty", "worker.execution"),
            OffsetDateTime.now().toString());
    }

    @GetMapping("/workers")
    public Workers workers() {
        return new Workers(List.of(
            new WorkerEntry("worker.harness", "not-declared", 4),
            new WorkerEntry("worker.pty", "not-declared", 4),
            new WorkerEntry("worker.execution", "not-declared", 4)),
            "nexus-core-jvm");
    }

    public record Health(String status, String service, String namespace,
                         List<String> workers, String timestamp) {}

    public record Workers(List<WorkerEntry> workers, String nodeID) {}

    public record WorkerEntry(String name, String status, int wave) {}
}