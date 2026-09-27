package org.nexus.peb.core.engine;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.nexus.peb.bootstrap.PebApplication;
import org.nexus.peb.domain.dto.AdmissionResponse;
import org.nexus.peb.domain.entity.PebTransaction;
import org.nexus.peb.domain.entity.PebViolation;
import org.nexus.peb.domain.enums.AdmissionPath;
import org.nexus.peb.domain.enums.AdmissionResult;
import org.nexus.peb.store.repository.PebTransactionRepository;
import org.nexus.peb.store.repository.PebViolationRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Integration test that exercises the real {@link PebGovernanceEngine} with
 * a live PostgreSQL connection and verifies that every admission path writes
 * the correct audit row to {@code peb.transactions}.
 *
 * <p>The {@link PebGovernanceEngine} is wired with all real collaborators
 * (not mocked), so this test validates actual database writes. Each test
 * method runs within a {@link Transactional @Transactional} boundary that
 * is rolled back after the test, leaving no residue in the database.
 *
 * <p>Paths under test:
 * <ol>
 *   <li>{@link AdmissionPath#VALIDATE} — {@code peb_validate_transition} →
 *       {@link AdmissionResult#ALLOWED}</li>
 *   <li>{@link AdmissionPath#MUTATE} — {@code peb_record_decision} →
 *       {@link AdmissionResult#ALLOWED}</li>
 *   <li>{@link AdmissionPath#REPORT_VIOLATION} — {@code peb_report_violation} →
 *       {@link AdmissionResult#REJECTED} + first-class row in
 *       {@code peb.violations}</li>
 *   <li>{@link AdmissionPath#UNKNOWN} — unrecognized tool name →
 *       {@link AdmissionResult#ROUTED}</li>
 * </ol>
 *
 * <p>Shared datasource properties mirror the existing
 * {@link org.nexus.peb.api.controller.AdmissionControllerFacadeTest}.
 */
@SpringBootTest(
    classes = PebApplication.class,
    webEnvironment = SpringBootTest.WebEnvironment.MOCK
)
@TestPropertySource(properties = {
    "spring.jackson.visibility.field=any",
    "spring.jackson.visibility.getter=any",
    "spring.jackson.visibility.setter=any",
    "spring.jackson.visibility.creator=any",
    // Dedicated throwaway database — see AdmissionControllerFacadeTest for
    // the rationale (fresh-database bootstrap via Flyway; no dependency on
    // the host's persistent nexus DB). Locally: createdb nexus_peb_test once.
    "spring.datasource.url=jdbc:postgresql://${PEB_TEST_PG_HOST:localhost}:5432/${PEB_TEST_PG_DB:nexus_peb_test}",
    "spring.datasource.username=pguser",
    "spring.datasource.password=pgpass",
    "spring.flyway.enabled=true",
    "spring.jpa.hibernate.ddl-auto=validate",
})
@Transactional
class PebGovernanceEngineAuditTest {

    @Autowired
    private PebGovernanceEngine engine;

    @Autowired
    private PebTransactionRepository transactionRepository;

    @Autowired
    private PebViolationRepository violationRepository;

    @Autowired
    private ObjectMapper objectMapper;

    // ---------------------------------------------------------------
    // 1. VALIDATE path
    // ---------------------------------------------------------------

    @Test
    @DisplayName("VALIDATE path writes ALLOWED audit row")
    void validatePath_writesAllowedAuditRow() throws Exception {
        PebTransaction tx = buildTransaction("peb_validate_transition", "{}");
        AdmissionPath path = AdmissionPath.fromToolName(tx.getToolName());

        AdmissionResponse response = engine.processForPath(tx, path);

        assertAll(
            () -> assertEquals(AdmissionPath.VALIDATE, path,
                "toolName peb_validate_transition maps to VALIDATE"),
            () -> assertTrue(response.admitted(),
                "VALIDATE path should be admitted"),
            () -> assertEquals("Validation processed", response.message(),
                "VALIDATE path returns expected message"),

            // Verify the engine set the correct admission result on the object
            () -> assertEquals(AdmissionResult.ALLOWED, tx.getAdmissionResult(),
                "Engine sets admissionResult to ALLOWED for VALIDATE"),

            // Verify the row was actually persisted to the database
            () -> {
                PebTransaction saved = transactionRepository.findById(tx.getId())
                    .orElseThrow(() -> new AssertionError("VALIDATE row not found in DB"));
                assertEquals(AdmissionResult.ALLOWED, saved.getAdmissionResult(),
                    "DB row has admission_result = ALLOWED");
                assertEquals("peb_validate_transition", saved.getToolName(),
                    "DB row has correct tool_name");
                assertNotNull(saved.getCreatedAt(),
                    "DB row has non-null created_at");
                assertNotNull(saved.getCommittedAt(),
                    "DB row has non-null committed_at");
            }
        );
    }

    // ---------------------------------------------------------------
    // 2. MUTATE path
    // ---------------------------------------------------------------

    @Test
    @DisplayName("MUTATE path writes ALLOWED audit row")
    void mutatePath_writesAllowedAuditRow() throws Exception {
        PebTransaction tx = buildTransaction("peb_record_decision", "{}");
        AdmissionPath path = AdmissionPath.fromToolName(tx.getToolName());

        AdmissionResponse response = engine.processForPath(tx, path);

        assertAll(
            () -> assertEquals(AdmissionPath.MUTATE, path,
                "toolName peb_record_decision maps to MUTATE"),
            () -> assertTrue(response.admitted(),
                "MUTATE path should be admitted"),
            () -> assertEquals("Mutation processed", response.message(),
                "MUTATE path returns expected message"),
            () -> assertEquals(AdmissionResult.ALLOWED, tx.getAdmissionResult(),
                "Engine sets admissionResult to ALLOWED for MUTATE"),
            () -> {
                PebTransaction saved = transactionRepository.findById(tx.getId())
                    .orElseThrow(() -> new AssertionError("MUTATE row not found in DB"));
                assertEquals(AdmissionResult.ALLOWED, saved.getAdmissionResult(),
                    "DB row has admission_result = ALLOWED");
                assertEquals("peb_record_decision", saved.getToolName(),
                    "DB row has correct tool_name");
                assertNotNull(saved.getCreatedAt(),
                    "DB row has non-null created_at");
                assertNotNull(saved.getCommittedAt(),
                    "DB row has non-null committed_at");
            }
        );
    }

    // ---------------------------------------------------------------
    // 3. REPORT_VIOLATION path
    // ---------------------------------------------------------------

    @Test
    @DisplayName("REPORT_VIOLATION writes REJECTED audit row + violation row")
    void reportViolationPath_writesRejectedAuditRowAndViolationRow() throws Exception {
        // Build a valid violation input matching the MCP facade schema
        ObjectNode violationInput = objectMapper.createObjectNode();
        violationInput.put("violation_type", "authority_leakage");
        violationInput.put("severity", "hard");
        violationInput.put("capability_attempted", "peb_force_transition");

        PebTransaction tx = buildTransaction("peb_report_violation",
            objectMapper.writeValueAsString(violationInput));
        AdmissionPath path = AdmissionPath.fromToolName(tx.getToolName());

        AdmissionResponse response = engine.processForPath(tx, path);

        assertAll(
            () -> assertEquals(AdmissionPath.REPORT_VIOLATION, path,
                "toolName peb_report_violation maps to REPORT_VIOLATION"),
            () -> assertTrue(response.admitted(),
                "REPORT_VIOLATION should be admitted (violation was recorded)"),
            () -> assertEquals("Violation recorded as REJECTED", response.message(),
                "REPORT_VIOLATION path returns expected message"),
            () -> assertEquals(AdmissionResult.REJECTED, tx.getAdmissionResult(),
                "Engine sets admissionResult to REJECTED for REPORT_VIOLATION"),

            // Verify the audit row
            () -> {
                PebTransaction saved = transactionRepository.findById(tx.getId())
                    .orElseThrow(() -> new AssertionError("REPORT_VIOLATION audit row not found in DB"));
                assertEquals(AdmissionResult.REJECTED, saved.getAdmissionResult(),
                    "DB audit row has admission_result = REJECTED");
                assertEquals("peb_report_violation", saved.getToolName(),
                    "DB audit row has correct tool_name");
                assertNotNull(saved.getCommittedAt(),
                    "DB audit row has non-null committed_at (violation was recorded)");
            },

            // Verify the first-class violation row was also written
            () -> {
                Optional<PebViolation> optViolation = violationRepository.findAll()
                    .stream()
                    .filter(v -> v.getTransactionId().equals(tx.getId()))
                    .findFirst();
                assertTrue(optViolation.isPresent(),
                    "REPORT_VIOLATION should produce a PebViolation row");
                PebViolation violation = optViolation.get();
                assertEquals(tx.getId(), violation.getTransactionId(),
                    "Violation references the correct transaction");
                assertNotNull(violation.getViolationType(),
                    "Violation has non-null violation_type");
                assertNotNull(violation.getSeverity(),
                    "Violation has non-null severity");
                assertNotNull(violation.getCreatedAt(),
                    "Violation has non-null created_at");
            }
        );
    }

    // ---------------------------------------------------------------
    // 4. UNKNOWN path
    // ---------------------------------------------------------------

    @Test
    @DisplayName("UNKNOWN path writes REJECTED audit row")
    void unknownPath_writesRejectedAuditRow() throws Exception {
        PebTransaction tx = buildTransaction("peb_nonexistent_tool", "{}");
        AdmissionPath path = AdmissionPath.fromToolName(tx.getToolName());

        AdmissionResponse response = engine.processForPath(tx, path);

        assertAll(
            () -> assertEquals(AdmissionPath.UNKNOWN, path,
                "unrecognized toolName maps to UNKNOWN"),
            () -> assertFalse(response.admitted(),
                "UNKNOWN path must fail closed"),
            () -> assertEquals("Admission denied by invariant validator", response.message(),
                "UNKNOWN path returns the validator rejection"),
            () -> assertEquals(AdmissionResult.REJECTED, tx.getAdmissionResult(),
                "Engine sets admissionResult to REJECTED for UNKNOWN"),
            () -> {
                PebTransaction saved = transactionRepository.findById(tx.getId())
                    .orElseThrow(() -> new AssertionError("UNKNOWN row not found in DB"));
                assertEquals(AdmissionResult.REJECTED, saved.getAdmissionResult(),
                    "DB row has admission_result = REJECTED");
                assertEquals("peb_nonexistent_tool", saved.getToolName(),
                    "DB row has correct tool_name");
                assertNotNull(saved.getCreatedAt(),
                    "DB row has non-null created_at");
                assertNotNull(saved.getCommittedAt(),
                    "DB row has non-null committed_at");
            }
        );
    }

    // ---------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------

    /**
     * Build a {@link PebTransaction} via Jackson deserialization, mirroring
     * the real request path through {@code AdmissionControllerFacade}.
     *
     * <p>{@link PebTransaction} declares getters/setters only for the fields
     * the engine dispatch needs; the remaining fields (including {@code id},
     * {@code idempotencyKey}, {@code entityId}, {@code toolName}, and
     * {@code input}) are set via Jackson field-level visibility, configured
     * through {@code spring.jackson.visibility.field=any} in the test
     * properties above.
     *
     * @param toolName  MCP tool name (drives {@link AdmissionPath} routing).
     * @param inputJson JSON string for the transaction's {@code input} payload.
     *                  Pass {@code "{}"} for an empty payload.
     */
    private PebTransaction buildTransaction(String toolName, String inputJson) throws Exception {
        String uuid = UUID.randomUUID().toString();
        String key = UUID.randomUUID().toString();
        String json = String.format(
            "{\"id\":\"%s\",\"idempotencyKey\":\"%s\",\"entityId\":\"%s\",\"toolName\":\"%s\",\"input\":%s,\"createdAt\":\"%s\"}",
            uuid, key, "audit-integration-test", toolName,
            inputJson != null ? inputJson : "{}",
            Instant.now().toString()
        );
        return objectMapper.readValue(json, PebTransaction.class);
    }
}
