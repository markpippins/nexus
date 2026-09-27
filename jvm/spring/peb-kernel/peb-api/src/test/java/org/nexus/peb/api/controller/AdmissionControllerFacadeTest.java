package org.nexus.peb.api.controller;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.nexus.peb.core.engine.PebGovernanceEngine;
import org.nexus.peb.core.violation.PebViolationEngine;
import org.nexus.peb.domain.dto.AdmissionResponse;
import org.nexus.peb.domain.entity.PebTransaction;
import org.nexus.peb.domain.enums.AdmissionPath;
import org.nexus.peb.domain.enums.AdmissionResult;
import org.nexus.peb.domain.exception.MalformedAdmissionRequestException;
import org.nexus.peb.store.repository.PebTransactionRepository;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.util.ReflectionTestUtils;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Tests AdmissionControllerFacade using a hand-written stub for
 * PebGovernanceEngine (Mockito is incompatible with Java 25 on this class).
 */
@DisplayName("AdmissionControllerFacade")
class AdmissionControllerFacadeTest {

    /**
     * Manual stub — overrides processForPath to return controlled responses
     * without needing Mockito bytecode instrumentation.
     */
    private static class StubGovernanceEngine extends PebGovernanceEngine {
        AdmissionResponse nextResponse;
        RuntimeException nextException;

        StubGovernanceEngine() {
            super(null, null, null, null, null);
        }

        @Override
        public AdmissionResponse processForPath(PebTransaction request, AdmissionPath path) {
            if (nextException != null) {
                RuntimeException ex = nextException;
                nextException = null;
                throw ex;
            }
            return nextResponse;
        }
    }

    private StubGovernanceEngine governanceEngine;
    private PebTransactionRepository transactionRepository;
    private AdmissionControllerFacade controller;
    /** When non-null, the repository reports this as a previously recorded transaction. */
    private PebTransaction replayTransaction;

    /**
     * Stub for the idempotency repository built on a JDK dynamic Proxy — the
     * Mockito-free equivalent of a mock (Mockito is incompatible with Java 25
     * on these classes, same rationale as StubGovernanceEngine). Only the two
     * methods the controller exercises are implemented; anything else fails
     * loudly instead of silently doing nothing.
     */
    private static PebTransactionRepository newTransactionRepository(
            java.util.function.Supplier<PebTransaction> existingSupplier) {
        return (PebTransactionRepository) java.lang.reflect.Proxy.newProxyInstance(
                PebTransactionRepository.class.getClassLoader(),
                new Class<?>[]{PebTransactionRepository.class},
                (proxy, method, args) -> {
                    switch (method.getName()) {
                        case "findByIdempotencyKey":
                            return java.util.Optional.ofNullable(existingSupplier.get());
                        case "toString":
                            return "RecordingTransactionRepository(proxy)";
                        case "hashCode":
                            return System.identityHashCode(proxy);
                        case "equals":
                            return proxy == args[0];
                        default:
                            throw new UnsupportedOperationException(
                                "stub does not implement " + method.getName());
                    }
                });
    }

    @BeforeEach
    void setUp() {
        governanceEngine = new StubGovernanceEngine();
        transactionRepository = newTransactionRepository(() -> replayTransaction);
        controller = new AdmissionControllerFacade(governanceEngine, transactionRepository);
    }

    /**
     * Builds a structurally complete transaction — all four persistence-required
     * fields present, exactly as peb-mcp sends them. Tests that target the
     * boundary guard then drop individual fields to assert 400.
     */
    private PebTransaction makeTransaction(String toolName) {
        PebTransaction tx = new PebTransaction();
        ReflectionTestUtils.setField(tx, "idempotencyKey", "test-key");
        ReflectionTestUtils.setField(tx, "entityId", "test-entity");
        ReflectionTestUtils.setField(tx, "toolName", toolName);
        ReflectionTestUtils.setField(tx, "input", JsonNodeFactory.instance.objectNode());
        return tx;
    }

    /** Complete transaction with one persistence-required field dropped (set to null). */
    private PebTransaction makeTransactionMissing(String missingField) {
        PebTransaction tx = makeTransaction("peb_validate_transition");
        ReflectionTestUtils.setField(tx, missingField, null);
        return tx;
    }

    private void stubAccepted(String message) {
        governanceEngine.nextResponse = AdmissionResponse.accepted(message);
    }

    private void stubDenied(String message) {
        governanceEngine.nextResponse = AdmissionResponse.denied(message);
    }

    private void stubException(RuntimeException ex) {
        governanceEngine.nextException = ex;
    }

    // ── GREEN PATH ──────────────────────────────────────────────

    @Nested
    @DisplayName("GreenPath - admission succeeds (200 OK)")
    class GreenPath {

        @Test
        @DisplayName("VALIDATE tool -> 200 OK")
        void validate_tool_returns200() {
            stubAccepted("Validated");
            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransaction("peb_validate_transition"));

            assertEquals(HttpStatus.OK, result.getStatusCode());
            assertJsonBody(result.getBody(), true, "Validated");
        }

        @Test
        @DisplayName("MUTATE tool -> 200 OK")
        void mutate_tool_returns200() {
            stubAccepted("Mutated");
            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransaction("peb_record_decision"));

            assertEquals(HttpStatus.OK, result.getStatusCode());
            assertJsonBody(result.getBody(), true, "Mutated");
        }

        @Test
        @DisplayName("REPORT_VIOLATION tool -> 200 OK")
        void reportViolation_tool_returns200() {
            stubAccepted("Violation recorded");
            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransaction("peb_report_violation"));

            assertEquals(HttpStatus.OK, result.getStatusCode());
        }

        @Test
        @DisplayName("all VALIDATE tools (3) work")
        void all_validate_tools() {
            String[] tools = {"peb_validate_transition", "peb_check_invariants", "peb_validate_transform"};
            for (String tool : tools) {
                stubAccepted("ok");
                assertEquals(HttpStatus.OK,
                        controller.submitTransaction(makeTransaction(tool)).getStatusCode());
            }
        }

        @Test
        @DisplayName("all MUTATE tools (4) work")
        void all_mutate_tools() {
            String[] tools = {"peb_record_decision", "peb_append_trace_segment",
                    "peb_request_clarification", "peb_extension_proposal"};
            for (String tool : tools) {
                stubAccepted("ok");
                assertEquals(HttpStatus.OK,
                        controller.submitTransaction(makeTransaction(tool)).getStatusCode());
            }
        }
    }

    // ── ORANGE PATH ─────────────────────────────────────────────

    @Nested
    @DisplayName("OrangePath - UNKNOWN toolName")
    class OrangePath {

        @Test
        @DisplayName("unrecognized (but present) toolName with complete fields -> engine decides")
        void unknown_tool_200_if_admitted() {
            stubAccepted("ROUTED: unknown tool");
            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransaction("some_random_tool"));

            assertEquals(HttpStatus.OK, result.getStatusCode());
            assertJsonBody(result.getBody(), true, "ROUTED: unknown tool");
        }
    }

    // ── IDEMPOTENT REPLAY PATH (parity with the Python kernel's api.py) ─

    @Nested
    @DisplayName("ReplayPath - idempotency-key replays mirror the Python kernel")
    class ReplayPath {

        @Test
        @DisplayName("same key + same payload -> recorded outcome replayed (200, original id)")
        void same_payload_replays_recorded_outcome() {
            stubAccepted("Mutated");
            controller.submitTransaction(makeTransaction("peb_record_decision"));

            PebTransaction prior = new PebTransaction();
            ReflectionTestUtils.setField(prior, "idempotencyKey", "test-key");
            ReflectionTestUtils.setField(prior, "entityId", "test-entity");
            ReflectionTestUtils.setField(prior, "toolName", "peb_record_decision");
            ReflectionTestUtils.setField(prior, "input",
                    JsonNodeFactory.instance.objectNode());
            prior.ensureId();
            prior.setAdmissionResult(AdmissionResult.ALLOWED);
            replayTransaction = prior;

            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransaction("peb_record_decision"));

            assertEquals(HttpStatus.OK, result.getStatusCode());
            JsonNode body = readJson(result.getBody());
            assertEquals(prior.getId().toString(), body.path("transaction_id").asText());
            assertEquals("ALLOWED", body.path("admission_result").asText());
            assertTrue(body.path("admitted").asBoolean());
        }

        @Test
        @DisplayName("same key + different payload -> 409 conflict")
        void conflicting_payload_is_409() {
            PebTransaction prior = new PebTransaction();
            ReflectionTestUtils.setField(prior, "idempotencyKey", "test-key");
            ReflectionTestUtils.setField(prior, "entityId", "test-entity");
            ReflectionTestUtils.setField(prior, "toolName", "peb_record_decision");
            ReflectionTestUtils.setField(prior, "input",
                    JsonNodeFactory.instance.objectNode().put("different", true));
            prior.ensureId();
            replayTransaction = prior;

            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransaction("peb_record_decision"));

            assertEquals(HttpStatus.CONFLICT, result.getStatusCode());
            assertFalse(readJson(result.getBody()).path("admitted").asBoolean());
        }
    }

    // ── JSON body contract helpers ──────────────────────────────

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private static JsonNode readJson(String body) {
        try {
            return MAPPER.readTree(body);
        } catch (Exception ex) {
            throw new AssertionError("Response body is not valid JSON: " + body, ex);
        }
    }

    /** Asserts the Python-kernel contract shape: {transaction_id, admission_result, message, admitted}. */
    private static void assertJsonBody(String body, boolean admitted, String message) {
        JsonNode json = readJson(body);
        assertTrue(json.hasNonNull("transaction_id") && !json.path("transaction_id").asText().isEmpty());
        assertEquals(admitted, json.path("admitted").asBoolean());
        assertEquals(message, json.path("message").asText());
        assertTrue(json.has("admission_result"));
    }

    // ── MALFORMED PATH ──────────────────────────────────────────

    @Nested
    @DisplayName("MalformedPath - missing persistence-required fields -> 400 Bad Request")
    class MalformedPath {

        @Test
        @DisplayName("null body -> 400")
        void null_body_400() {
            ResponseEntity<String> result = controller.submitTransaction(null);

            assertEquals(HttpStatus.BAD_REQUEST, result.getStatusCode());
            assertTrue(result.getBody().contains("transaction body"));
        }

        @Test
        @DisplayName("null toolName -> 400 (was: stale 200 ROUTED intent)")
        void null_toolName_400() {
            ResponseEntity<String> result = controller.submitTransaction(makeTransaction(null));

            assertEquals(HttpStatus.BAD_REQUEST, result.getStatusCode());
            assertTrue(result.getBody().contains("toolName"));
        }

        @Test
        @DisplayName("empty toolName -> 400")
        void empty_toolName_400() {
            ResponseEntity<String> result = controller.submitTransaction(makeTransaction(""));

            assertEquals(HttpStatus.BAD_REQUEST, result.getStatusCode());
            assertTrue(result.getBody().contains("toolName"));
        }

        @Test
        @DisplayName("missing idempotencyKey -> 400")
        void missing_idempotencyKey_400() {
            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransactionMissing("idempotencyKey"));

            assertEquals(HttpStatus.BAD_REQUEST, result.getStatusCode());
            assertTrue(result.getBody().contains("idempotencyKey"));
        }

        @Test
        @DisplayName("missing entityId -> 400")
        void missing_entityId_400() {
            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransactionMissing("entityId"));

            assertEquals(HttpStatus.BAD_REQUEST, result.getStatusCode());
            assertTrue(result.getBody().contains("entityId"));
        }

        @Test
        @DisplayName("missing input -> 400")
        void missing_input_400() {
            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransactionMissing("input"));

            assertEquals(HttpStatus.BAD_REQUEST, result.getStatusCode());
            assertTrue(result.getBody().contains("input"));
        }
    }

    // ── RED PATH ────────────────────────────────────────────────

    @Nested
    @DisplayName("RedPath - engine rejects or throws")
    class RedPath {

        @Test
        @DisplayName("non-admitted -> 422 Unprocessable Entity")
        void engine_rejects_returns422() {
            stubDenied("Invariant violation: hash mismatch");
            ResponseEntity<String> result = controller.submitTransaction(
                    makeTransaction("peb_validate_transition"));

            assertEquals(HttpStatus.UNPROCESSABLE_ENTITY, result.getStatusCode());
            assertJsonBody(result.getBody(), false, "Invariant violation: hash mismatch");
        }

        @Test
        @DisplayName("MalformedAdmissionRequestException propagates (handler tested via @WebMvcTest)")
        void malformedAdmission_propagates() {
            // NOTE: @ExceptionHandler only fires inside Spring MVC DispatcherServlet.
            // Direct POJO call propagates the exception. Spring MVC integration
            // (that it returns 422) is tested via @WebMvcTest (requires Mockito on Java 25).
            stubException(new MalformedAdmissionRequestException("missing violation_type"));

            MalformedAdmissionRequestException ex = assertThrows(
                    MalformedAdmissionRequestException.class,
                    () -> controller.submitTransaction(makeTransaction("peb_report_violation")));
            assertTrue(ex.getMessage().contains("missing violation_type"));
        }

        @Test
        @DisplayName("RuntimeException propagates (not masked as 422)")
        void runtimeException_propagates() {
            stubException(new RuntimeException("DB connection lost"));

            RuntimeException ex = assertThrows(RuntimeException.class,
                    () -> controller.submitTransaction(makeTransaction("peb_validate_transition")));
            assertEquals("DB connection lost", ex.getMessage());
        }
    }
}
