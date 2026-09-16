package org.nexus.solscript.api;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * Read-only posture for the SOLScript facade WRITE ops.
 *
 * The interpreter library fully implements state transitions, but the REST
 * tier DISALLOWS mutation: POST /api/solscript/transition-entity returns 405
 * until the JetStream write path lands (writes queue for reconciliation on
 * return to the home network). Querying and evaluation stay live on the read
 * controller — this is the readonly-by-default boundary.
 */
@RestController
@RequestMapping("/api/solscript")
public class SolScriptWriteController {

    @PostMapping("/transition-entity")
    public Object transitionEntity(@RequestBody(required = false) Object body) {
        throw readOnly("POST /api/solscript/transition-entity");
    }

    private static ResponseStatusException readOnly(String surface) {
        return new ResponseStatusException(
            HttpStatus.METHOD_NOT_ALLOWED,
            "nexus-core is read-only: " + surface
                + " mutates entity state. Disallowed in the read-only projection; "
                + "write path (JetStream) not yet implemented.");
    }
}