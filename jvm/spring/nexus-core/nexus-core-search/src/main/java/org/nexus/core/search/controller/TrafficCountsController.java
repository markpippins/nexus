package org.nexus.core.search.controller;

import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Read-only projection of `moleculer` GET /api/traffic/counts (M1 traffic canary).
 *
 * In the source service this derives per-action request counts from the built-in
 * moleculer metrics registry ({$node.metrics}). The JVM projection has no
 * moleculer broker in-process, so it reports an explicitly-empty window rather
 * than fabricating counts — the canary observation lives on the live TS service.
 * Shape: {startedAt, total, counts}.
 */
@RestController
@RequestMapping("/search/api")
public class TrafficCountsController {

    @GetMapping("/traffic/counts")
    public TrafficCounts trafficCounts() {
        return new TrafficCounts(OffsetNow(), 0, Map.of());
    }

    private static String OffsetNow() {
        return java.time.OffsetDateTime.now().toString();
    }

    /** Contract `TrafficCounts` — {startedAt, total, counts}. */
    public record TrafficCounts(String startedAt, int total, Map<String, Integer> counts) {}
}