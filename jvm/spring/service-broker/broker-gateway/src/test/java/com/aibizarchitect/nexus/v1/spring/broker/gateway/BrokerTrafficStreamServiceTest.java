package com.aibizarchitect.nexus.v1.spring.broker.gateway;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Map;

import org.junit.jupiter.api.Test;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import com.aibizarchitect.nexus.v1.broker.api.BrokerTrafficEvent;

class BrokerTrafficStreamServiceTest {

    @Test
    void subscribeRegistersEmitterAndCompletionRemovesIt() {
        BrokerTrafficStreamService service = new BrokerTrafficStreamService();

        SseEmitter emitter = service.subscribe();

        assertNotNull(emitter);
        assertEquals(1, service.subscriberCount());

        service.shutdown();
        assertEquals(0, service.subscriberCount());
    }

    @Test
    void multipleSubscribersCanBeRegisteredTogether() {
        BrokerTrafficStreamService service = new BrokerTrafficStreamService();

        SseEmitter first = service.subscribe();
        SseEmitter second = service.subscribe();

        assertNotNull(first);
        assertNotNull(second);
        assertEquals(2, service.subscriberCount());

        service.publish(new BrokerTrafficEvent(
                "evt-1",
                "2026-04-23T00:00:00Z",
                5L,
                "req-1",
                "loginService",
                "login",
                true,
                200,
                "BrokerController.submitRequest",
                null,
                null,
                null));
        service.sendHeartbeat();

        service.shutdown();
        assertEquals(0, service.subscriberCount());
    }

    // ── M1 traffic canary ────────────────────────────────────────────

    @Test
    @SuppressWarnings("unchecked")
    void publishCountsInvocationsPerServiceOperationWithoutSubscribers() {
        BrokerTrafficStreamService service = new BrokerTrafficStreamService();
        try {
            // No subscribers: counting must not depend on SSE fanout.
            assertEquals(0, service.subscriberCount());

            service.publish(trafficEvent("googleSearchService", "simpleSearch"));
            service.publish(trafficEvent("googleSearchService", "simpleSearch"));
            service.publish(trafficEvent("googleSearchService", "forceSearch"));

            assertEquals(2L, service.invocationCount("googleSearchService", "simpleSearch"));
            assertEquals(1L, service.invocationCount("googleSearchService", "forceSearch"));
            assertEquals(0L, service.invocationCount("googleSearchService", "neverCalled"));

            Map<String, Object> snapshot = service.trafficSnapshot();
            assertNotNull(snapshot.get("startedAt"));
            assertEquals(3L, snapshot.get("total"));
            Map<String, Long> counts = (Map<String, Long>) snapshot.get("counts");
            assertEquals(2L, counts.get("googleSearchService/simpleSearch"));
            assertEquals(1L, counts.get("googleSearchService/forceSearch"));
        } finally {
            service.shutdown();
        }
    }

    @Test
    void nullServiceOrOperationCountedAsUnknown() {
        BrokerTrafficStreamService service = new BrokerTrafficStreamService();
        try {
            service.publish(trafficEvent(null, null));

            // Nulls normalize to the same unknown/unknown key (never NPE,
            // never silently dropped).
            assertEquals(1L, service.invocationCount("unknown", "unknown"));
            assertEquals(1L, service.invocationCount(null, null));
        } finally {
            service.shutdown();
        }
    }

    @Test
    void freshInstanceStartsWithEmptyWindow() {
        BrokerTrafficStreamService service = new BrokerTrafficStreamService();
        try {
            // Documents the restart-reset semantics the observation procedure
            // relies on: a fresh boot is an empty window, never stale counts.
            Map<String, Object> snapshot = service.trafficSnapshot();
            assertEquals(0L, snapshot.get("total"));
            assertTrue(((Map<?, ?>) snapshot.get("counts")).isEmpty());
            assertNotNull(service.trafficWindowStart());
        } finally {
            service.shutdown();
        }
    }

    private static BrokerTrafficEvent trafficEvent(String service, String operation) {
        return new BrokerTrafficEvent(
                "evt-1",
                "2026-04-23T00:00:00Z",
                5L,
                "req-1",
                service,
                operation,
                true,
                200,
                "BrokerController.submitRequest",
                null,
                null,
                null);
    }
}
