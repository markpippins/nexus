package org.nexus.core.wrp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.nexus.core.wrp.addressing.Addressing;
import org.nexus.core.wrp.engine.KernelEngine;
import org.nexus.core.wrp.identity.CcnfIdentity;
import org.nexus.core.wrp.kernel.Kernel;
import org.nexus.core.wrp.states.States;

class WrpKernelTest {

    // ---- states: canonical adjacency ------------------------------------

    @Test
    void legalTransitionsPassAndIllegalFail() {
        assertTrue(States.isValidTransition("CREATED", "INTAKE"));
        assertTrue(States.isValidTransition("CRITIQUE", "PLANNING")); // reject loop
        assertTrue(States.isValidTransition("EXECUTING", "COMPLETED"));
        assertTrue(!States.isValidTransition("CREATED", "FAILED")); // per TS canonical
        assertTrue(!States.isValidTransition("COMPLETED", "FAILED")); // terminal success
        assertTrue(!States.isValidTransition("INTAKE", "EXECUTING")); // skip-ahead
    }

    @Test
    void receiptMappingMatchesCanonical() {
        assertEquals("INTAKE", States.receiptToWrpState("PLANNING"));
        assertEquals("PLANNING", States.receiptToWrpState("CRITIQUE_REJECT"));
        assertEquals("COMPLETED", States.receiptToWrpState("REVIEW_PASS"));
        assertEquals("EXECUTING", States.receiptToWrpState("REVIEW_REJECT"));
        assertEquals("FAILED", States.receiptToWrpState("API_LIMIT"));
        assertEquals("ARCHIVED", States.receiptToWrpState("CANCELLED"));
        assertThrows(WrpException.class, () -> States.receiptToWrpState("NOT_A_RECEIPT"));
    }

    // ---- addressing: CAL format ------------------------------------------

    @Test
    void calAddressIsDeterministicAndParses() {
        String a1 = Addressing.makeAddress("dev", "my-pipeline", "t0", "transform", null);
        String a2 = Addressing.makeAddress("dev", "my-pipeline", "t0", "transform", null);
        assertEquals(a1, a2);
        assertTrue(a1.startsWith("cal://dev/my-pipeline/t0/transform/"));
        Map<String, String> parts = Addressing.parseAddress(a1);
        assertEquals("dev", parts.get("realm"));
        assertEquals("transform", parts.get("node_id"));
        assertNull(Addressing.parseAddress("http://not-cal"));
        assertNull(Addressing.parseAddress("cal://only/two"));
        assertEquals(Addressing.contentHash("dev/my-pipeline/t0/transform"), parts.get("version"));
    }

    // ---- identity: CCNF canonical JSON + entity_key ----------------------

    @Test
    void canonicalJsonMatchesGoSerializerRules() {
        assertEquals("null", CcnfIdentity.canonicalJson(null));
        assertEquals("true", CcnfIdentity.canonicalJson(true));
        assertEquals("7", CcnfIdentity.canonicalJson(7));
        assertEquals("{\"a\":1,\"b\":[1,2],\"c\":\"x\"}", CcnfIdentity.canonicalJson(Map.of("c", "x", "a", 1, "b", List.of(1, 2))));
        // Control chars below 0x20 escape as backslash-u00XX; sorted keys; no spaces.
        assertEquals("{\"k\":\"a\\u0001b\"}", CcnfIdentity.canonicalJson(Map.of("k", "a\u0001b")));
    }

    @Test
    void controlledVocabularyEnforcedAndEmitMatchesDerive() {
        Map<String, Object> doc = CcnfIdentity.wrnDocFixed();
        CcnfIdentity.Identity emitted = CcnfIdentity.emitIdentity(doc);
        assertEquals("event", emitted.eventType());
        assertEquals("executiongraph.v2", emitted.scope());
        // Deterministic: same doc → same key.
        assertEquals(emitted.entityKey(), CcnfIdentity.emitIdentity(CcnfIdentity.wrnDocFixed()).entityKey());
        // Free-text intent cannot be normalized.
        assertThrows(WrpException.class, () -> CcnfIdentity.normalizeIntent("do a thing"));
        assertThrows(WrpException.class, () -> CcnfIdentity.normalizeIntent(Map.of("action", "vibes")));
    }

    // ---- engine: reduce pipeline ------------------------------------------

    @Test
    void reduceAppliesLegalSequenceAndIncrementsVersion() {
        Kernel.KernelState state = new Kernel.KernelState();
        Kernel.KernelDelta delta = new Kernel.KernelDelta("d1", "b1",
                List.of(Map.of("receipt_id", "r1", "type", "PLANNING", "plan_id", "p1")),
                null, null, 1);
        Kernel.KernelResult result = KernelEngine.reduce(state, delta);
        assertTrue(result.isOk());
        assertEquals(1, result.value().version());
        assertTrue(result.value().plans().contains("p1"));
        assertEquals("INTAKE", result.value().transitions().get(0).get("to_state"));
    }

    @Test
    void invalidTransitionYieldsFirstClassErrorNotException() {
        Kernel.KernelState state = new Kernel.KernelState();
        Kernel.KernelDelta delta = new Kernel.KernelDelta("d2", "b2",
                List.of(Map.of("receipt_id", "r9", "type", "REVIEW_PASS", "plan_id", "p1", "from_state", "CREATED")),
                null, null, 2);
        Kernel.KernelResult result = KernelEngine.reduce(state, delta);
        assertTrue(result.isError());
        assertEquals(Kernel.INVALID_TRANSITION, result.error().type());
    }

    @Test
    void noOpDeltaDoesNotIncrementVersion() {
        Kernel.KernelState state = new Kernel.KernelState();
        Kernel.KernelResult result = KernelEngine.reduce(state, new Kernel.KernelDelta("d3", "b3", List.of(), null, null, 3));
        assertTrue(result.isOk());
        assertEquals(0, result.value().version());
    }
}
