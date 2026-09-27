package org.nexus.peb.api.controller;

import org.nexus.peb.bootstrap.PebApplication;
import org.nexus.peb.core.engine.PebGovernanceEngine;
import org.nexus.peb.domain.dto.AdmissionResponse;
import org.nexus.peb.domain.exception.MalformedAdmissionRequestException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Integration slice covering the JSON deserialization of
 * {@link org.nexus.peb.domain.entity.PebTransaction} requests and the typed
 * {@code @ExceptionHandler(MalformedAdmissionRequestException.class) -> 422}
 * mapping on {@link AdmissionControllerFacade}.
 *
 * <p>Uses {@link SpringBootTest} (not {@code @WebMvcTest}) because
 * PebApplication's package is {@code org.nexus.peb.bootstrap} while the
 * controller is in {@code org.nexus.peb.api.controller} — a sibling
 * package that {@code @WebMvcTest}'s auto-scan cannot reach.
 * {@code @SpringBootTest} with {@code webEnvironment = MOCK} together
 * with {@code @AutoConfigureMockMvc} gives the same MockMvc behavior
 * with the full application context on the classpath.
 *
 * <p>The datasource connects directly to the running pgvector PostgreSQL
 * container on {@code localhost:5432} (database {@code nexus},
 * {@code currentSchema=peb}). Hibernate's {@code ddl-auto: validate}
 * confirms entity annotations align with the {@code peb} schema tables.
 * The {@code PebGovernanceEngine} is mocked, so no DB writes occur.
 *
 * <p>Two categories of 422 are tested here:
 * <ol>
 *   <li>Malformed admission request (e.g. {@code peb_report_violation}
 *       without a valid {@code violation_type}) — engine throws
 *       {@link MalformedAdmissionRequestException}, controller maps to 422.
 *   <li>Invariant-validator denial — engine returns
 *       {@link AdmissionResponse#denied(String)}, controller maps to 422.
 * </ol>
 *
 * <p>The boundary guard is also covered: requests missing any
 * persistence-required field ({@code idempotencyKey}, {@code entityId},
 * {@code toolName}, {@code input}) are rejected with 400 before reaching the
 * engine — regression for the production 500 on {@code POST {} }.
 */
@SpringBootTest(
    classes = PebApplication.class,
    webEnvironment = SpringBootTest.WebEnvironment.MOCK
)
@AutoConfigureMockMvc
@TestPropertySource(properties = {
    "spring.jackson.visibility.field=any",
    "spring.jackson.visibility.getter=any",
    "spring.jackson.visibility.setter=any",
    "spring.jackson.visibility.creator=any",
    // Dedicated throwaway database, NOT the shared nexus DB: CI runners (and
    // any dev machine) bootstrap nexus_peb_test via Flyway from the V1-V4
    // chain, so these tests exercise a real fresh-database bootstrap instead
    // of silently depending on whatever schema the host's persistent DB
    // happens to carry (the 2026-09-27 CI discovery). Override host/db via
    // PEB_TEST_PG_HOST / PEB_TEST_PG_DB if your test DB lives elsewhere.
    // CI creates nexus_peb_test in the build job; locally run once:
    //   createdb nexus_peb_test
    "spring.datasource.url=jdbc:postgresql://${PEB_TEST_PG_HOST:localhost}:5432/${PEB_TEST_PG_DB:nexus_peb_test}",
    "spring.datasource.username=pguser",
    "spring.datasource.password=pgpass",
    // Flyway bootstraps the throwaway DB from db/migration; Hibernate then
    // validates the entities against what actually flew.
    "spring.flyway.enabled=true",
    "spring.jpa.hibernate.ddl-auto=validate",
})
class AdmissionControllerFacadeTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private PebGovernanceEngine governanceEngine;

    /** Structurally complete transaction, shaped exactly as peb-mcp sends it. */
    private static final String COMPLETE_PAYLOAD =
        "{\"idempotencyKey\":\"test-key\",\"entityId\":\"test-entity\","
        + "\"toolName\":\"peb_validate_transition\",\"input\":{}}";

    private static final String COMPLETE_VIOLATION_PAYLOAD =
        "{\"idempotencyKey\":\"test-key\",\"entityId\":\"test-entity\","
        + "\"toolName\":\"peb_report_violation\",\"input\":{\"violation_type\":\"policy\"}}";

    @Test
    void malformedViolation_returns422() throws Exception {
        when(governanceEngine.processForPath(any(), any()))
            .thenThrow(new MalformedAdmissionRequestException(
                "peb_report_violation requires a textual 'violation_type' field"));

        mockMvc.perform(post("/api/v1/peb/transaction")
                .contentType(MediaType.APPLICATION_JSON)
                .content(COMPLETE_VIOLATION_PAYLOAD))
            .andExpect(status().isUnprocessableEntity())
            .andExpect(content().string(
                "Malformed admission request: "
                + "peb_report_violation requires a textual 'violation_type' field"));
    }

    @Test
    void validTransaction_returns200() throws Exception {
        when(governanceEngine.processForPath(any(), any()))
            .thenReturn(AdmissionResponse.accepted("Mutation processed"));

        mockMvc.perform(post("/api/v1/peb/transaction")
                .contentType(MediaType.APPLICATION_JSON)
                .content(COMPLETE_PAYLOAD))
            .andExpect(status().isOk())
            .andExpect(content().string("Mutation processed"));
    }

    @Test
    void validatorDenial_returns422() throws Exception {
        when(governanceEngine.processForPath(any(), any()))
            .thenReturn(AdmissionResponse.denied("Admission denied by invariant validator"));

        mockMvc.perform(post("/api/v1/peb/transaction")
                .contentType(MediaType.APPLICATION_JSON)
                .content(COMPLETE_PAYLOAD))
            .andExpect(status().isUnprocessableEntity())
            .andExpect(content().string("Admission denied by invariant validator"));
    }

    // ── Boundary guard: malformed input -> 400 (regression for the 500) ──

    @Test
    void emptyObject_returns400() throws Exception {
        mockMvc.perform(post("/api/v1/peb/transaction")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{}"))
            .andExpect(status().isBadRequest())
            .andExpect(content().string(
                "Malformed admission request: missing required field(s): "
                + "idempotencyKey, entityId, toolName, input"));
    }

    @Test
    void partialPayload_returns400() throws Exception {
        mockMvc.perform(post("/api/v1/peb/transaction")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"toolName\":\"peb_validate_transition\"}"))
            .andExpect(status().isBadRequest())
            .andExpect(content().string(
                "Malformed admission request: missing required field(s): "
                + "idempotencyKey, entityId, input"));
    }

    @Test
    void nullToolName_returns400() throws Exception {
        mockMvc.perform(post("/api/v1/peb/transaction")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"idempotencyKey\":\"k\",\"entityId\":\"e\",\"input\":{}}"))
            .andExpect(status().isBadRequest())
            .andExpect(content().string(
                "Malformed admission request: missing required field(s): toolName"));
    }
}
