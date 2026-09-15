package org.nexus.core.search.controller;

import java.time.OffsetDateTime;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Read-only projection of `moleculer` GET /api/health (TypeSpec contract op `health`).
 *
 * Mirrors the moleculer search gateway's health action: {status, service, timestamp}.
 *
 * NOTE on the monolith path: the search gateway is a DISTINCT service (runs
 * standalone on :4050 with contract path /api/health). Assembled into the
 * nexus-core monolith its surface is namespaced under /search so it does not
 * collide with the broker aggregate's /api/health. In its own deployment the
 * path is /api/health unchanged.
 */
@RestController
@RequestMapping("/search/api")
public class SearchHealthController {

    @GetMapping("/health")
    public Health health() {
        return new Health("ok", "moleculer-search", OffsetDateTime.now().toString());
    }

    /** Contract `MoleculerHealthResponse` — {status, service}. */
    public record Health(String status, String service, String timestamp) {}
}