package org.nexus.core.app;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Assembles the monolith and smoke-tests the read-only posture:
 *   - search health answers (real read)
 *   - a search write returns 405
 *   - a broker write returns 405
 *
 * Runs against the assembled context; DB-backed execution reads are not
 * exercised here (require a live `execution` schema).
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class NexusCoreApplicationTest {

    @Autowired
    TestRestTemplate rest;

    @Test
    void searchHealthIsReadOnlyReadable() {
        // Search surface is namespaced under /search in the monolith (distinct
        // service; standalone it serves /api/health).
        ResponseEntity<String> resp = rest.getForEntity("/search/api/health", String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody()).contains("moleculer-search");
    }

    @Test
    void trafficCountsReportsEmptyWindow() {
        ResponseEntity<String> resp = rest.getForEntity("/search/api/traffic/counts", String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody()).contains("\"total\":0");
    }

    @Test
    void searchWriteReturns405() {
        ResponseEntity<String> resp = rest.postForEntity(
            "/search/api/search/simple", new HttpEntity<>("{}", jsonHeaders()), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.METHOD_NOT_ALLOWED);
    }

    @Test
    void brokerWriteReturns405() {
        ResponseEntity<String> resp = rest.postForEntity(
            "/api/workers/harness/run", new HttpEntity<>("{}", jsonHeaders()), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.METHOD_NOT_ALLOWED);
    }

    @Test
    void solscriptHealthAnswers() {
        ResponseEntity<String> resp = rest.getForEntity("/api/solscript/health", String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(resp.getBody()).contains("solscript-java");
    }

    @Test
    void solscriptTransitionIsReadOnly405() {
        ResponseEntity<String> resp = rest.postForEntity(
            "/api/solscript/transition-entity",
            new HttpEntity<>("{}", jsonHeaders()), String.class);
        assertThat(resp.getStatusCode()).isEqualTo(HttpStatus.METHOD_NOT_ALLOWED);
    }

    private static org.springframework.http.HttpHeaders jsonHeaders() {
        var h = new org.springframework.http.HttpHeaders();
        h.setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
        return h;
    }
}