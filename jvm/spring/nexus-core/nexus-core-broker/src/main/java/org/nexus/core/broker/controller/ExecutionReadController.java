package org.nexus.core.broker.controller;

import java.util.Map;
import org.nexus.core.broker.execution.ExecutionReadService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Read-only projection of the `nexus-broker` execution read catalog.
 *
 * Mirrors the 18 GET /api/workers/execution/* routes from the TypeSpec
 * contract (typespec/v1/nexus-broker/typescript/operations.tsp) against the
 * real `execution` schema via {@link ExecutionReadService}. SELECTs only.
 */
@RestController
@RequestMapping("/api/workers/execution")
public class ExecutionReadController {

    private final ExecutionReadService svc;

    public ExecutionReadController(ExecutionReadService svc) {
        this.svc = svc;
    }

    @GetMapping
    public Map<String, Object> health() {
        return svc.health();
    }

    @GetMapping("/health")
    public Map<String, Object> richHealth() {
        Map<String, Object> h = svc.health();
        return Map.of("scanned_at", java.time.OffsetDateTime.now().toString(), "schema", "execution",
            "totals", h.get("counts"), "scans", Map.of());
    }

    @GetMapping("/requests")
    public Map<String, Object> listRequests(@RequestParam(defaultValue = "") String status,
                                            @RequestParam(defaultValue = "") String search,
                                            @RequestParam(defaultValue = "20") int limit,
                                            @RequestParam(defaultValue = "0") int offset) {
        return svc.list("requests", "status", status, search, clamp(limit), Math.max(0, offset));
    }

    @GetMapping("/leases")
    public Map<String, Object> listLeases(@RequestParam(defaultValue = "") String status,
                                          @RequestParam(defaultValue = "") String search,
                                          @RequestParam(defaultValue = "20") int limit,
                                          @RequestParam(defaultValue = "0") int offset) {
        return svc.list("leases", "status", status, search, clamp(limit), Math.max(0, offset));
    }

    @GetMapping("/attempts")
    public Map<String, Object> listAttempts(@RequestParam(defaultValue = "") String status,
                                            @RequestParam(defaultValue = "") String search,
                                            @RequestParam(defaultValue = "20") int limit,
                                            @RequestParam(defaultValue = "0") int offset) {
        return svc.list("attempts", "status", status, search, clamp(limit), Math.max(0, offset));
    }

    @GetMapping("/receipts")
    public Map<String, Object> listReceipts(@RequestParam(defaultValue = "") String type,
                                            @RequestParam(defaultValue = "") String search,
                                            @RequestParam(defaultValue = "20") int limit,
                                            @RequestParam(defaultValue = "0") int offset) {
        return svc.list("receipts", "type", type, search, clamp(limit), Math.max(0, offset));
    }

    @GetMapping("/requests/{id}/state")
    public Map<String, Object> requestState(@PathVariable String id) {
        return svc.requestState(id);
    }

    @GetMapping("/leases/stale")
    public Map<String, Object> staleLeases() {
        return svc.staleLeases();
    }

    @GetMapping("/leases/{id}/lifecycle")
    public Map<String, Object> leaseLifecycle(@PathVariable String id) {
        return svc.leaseLifecycle(id);
    }

    private static int clamp(int v) {
        return Math.min(100, Math.max(1, v));
    }
}