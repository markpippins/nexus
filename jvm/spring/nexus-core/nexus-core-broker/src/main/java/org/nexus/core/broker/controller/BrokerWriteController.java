package org.nexus.core.broker.controller;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * Read-only projection of the `nexus-broker` WRITE ops (keychain snapshot
 * triggers + worker-tier process control).
 *
 * Every one of these is a write-path surface (persists a snapshot, spawns/
 * kills a process, executes an agent run). In the local read-only projection
 * they are NOT available: they return 405 until the JetStream write path lands
 * (writes queue for reconciliation on return to the home network).
 *
 * This is the deliberate readonly-by-default posture — each 405 body states why.
 */
@RestController
@RequestMapping("/api")
public class BrokerWriteController {

    @PostMapping("/keychain-snapshot/snapshot")
    public Object keychainSnapshot(@RequestBody(required = false) Object body) {
        throw readOnly("POST /api/keychain-snapshot/snapshot");
    }

    @PostMapping("/keychain-snapshot/agent-records/snapshot")
    public Object keychainAgentRecordsSnapshot(@RequestBody(required = false) Object body) {
        throw readOnly("POST /api/keychain-snapshot/agent-records/snapshot");
    }

    @PostMapping("/workers/pty")
    public Object ptySpawn(@RequestBody(required = false) Object body) {
        throw readOnly("POST /api/workers/pty");
    }

    @DeleteMapping("/workers/pty/{id}")
    public Object ptyKill(@PathVariable String id) {
        throw readOnly("DELETE /api/workers/pty/" + id);
    }

    @PostMapping("/workers/harness/run")
    public Object harnessRun(@RequestBody(required = false) Object body) {
        throw readOnly("POST /api/workers/harness/run");
    }

    @PostMapping("/workers/harness/resolve-context")
    public Object harnessResolveContext(@RequestBody(required = false) Object body) {
        throw readOnly("POST /api/workers/harness/resolve-context");
    }

    private static ResponseStatusException readOnly(String surface) {
        return new ResponseStatusException(
            HttpStatus.METHOD_NOT_ALLOWED,
            "nexus-core is read-only: " + surface
                + " is a write-path surface. Not available in the local read-only projection; "
                + "write path (JetStream) not yet implemented.");
    }
}