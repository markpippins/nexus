package org.nexus.core.search.controller;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * Read-only projection of the `moleculer` search write ops.
 *
 * POST /api/search/simple and POST /api/search/force both perform a LIVE Google
 * Custom Search and write to the shared cache / rate-limiter — i.e. they are
 * write-path surfaces on the search domain. In the read-only local projection
 * they are NOT available: they return 405 until the JetStream write path lands.
 *
 * This is the deliberate readonly-by-default posture, not a stub to be filled
 * silently — the 405 body states exactly why.
 */
@RestController
@RequestMapping("/search/api/search")
public class SearchWriteController {

    @PostMapping("/simple")
    public Object simpleSearch(@RequestBody(required = false) Object body) {
        throw readOnly("POST /api/search/simple");
    }

    @PostMapping("/force")
    public Object forceSearch(@RequestBody(required = false) Object body) {
        throw readOnly("POST /api/search/force");
    }

    private static ResponseStatusException readOnly(String surface) {
        return new ResponseStatusException(
            HttpStatus.METHOD_NOT_ALLOWED,
            "nexus-core is read-only: " + surface
                + " is a write-path search surface (live provider + cache write). "
                + "Not available in the local read-only projection; write path (JetStream) not yet implemented.");
    }
}