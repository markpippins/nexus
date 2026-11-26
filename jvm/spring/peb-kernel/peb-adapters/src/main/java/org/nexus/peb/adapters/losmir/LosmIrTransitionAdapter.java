package org.nexus.peb.adapters.losmir;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.nexus.peb.domain.port.LosmIrTransitionPort;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

/**
 * REST client adapter for Vision/LOSM-IR transition system.
 * 
 * LOSM-host (Python FastAPI, port 8006) provides the work request
 * orchestration layer. LOSM-IR validates state transitions against
 * the lifecycle state machine (NEW -> INTAKE -> PLAN_GENERATION -> ...).
 * 
 * This adapter provides the write path for PEB → LOSM integration:
 * - Transition work requests through lifecycle states
 * - Query work request status
 * - Orchestrate work request processing
 */
@Component
public class LosmIrTransitionAdapter implements LosmIrTransitionPort {

    private static final Logger log = LoggerFactory.getLogger(LosmIrTransitionAdapter.class);

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;
    private final String baseUrl;

    public LosmIrTransitionAdapter(
            @Value("${peb.losm.url:http://localhost:8006}") String baseUrl,
            ObjectMapper objectMapper) {
        this.restTemplate = new RestTemplate();
        // Boot 4 / Spring 7 registers a Jackson 3 JSON converter by default.
        // The PEB domain model uses Jackson 2 JsonNode (Hibernate's JSON
        // mapping is Jackson 2), so put a Jackson 2 converter first.
        this.restTemplate.getMessageConverters()
                .add(0, new MappingJackson2HttpMessageConverter(objectMapper));
        this.objectMapper = objectMapper;
        this.baseUrl = baseUrl;
    }

    @Override
    public JsonNode transition(String wrId, String toState, String actor, String reason) {
        try {
            ObjectNode payload = objectMapper.createObjectNode();
            payload.put("to_state", toState);
            payload.put("actor", actor);
            payload.put("reason", reason);

            ResponseEntity<JsonNode> response = restTemplate.exchange(
                    baseUrl + "/work-requests/" + wrId + "/transition",
                    HttpMethod.POST,
                    new HttpEntity<>(payload, createHeaders()),
                    JsonNode.class
            );
            return response.getBody();
        } catch (RestClientException e) {
            log.error("Transition WR {} to {} failed: {}", wrId, toState, e.getMessage());
            throw e;
        }
    }

    @Override
    public JsonNode getWorkRequest(String wrId) {
        try {
            ResponseEntity<JsonNode> response = restTemplate.exchange(
                    baseUrl + "/work-requests/" + wrId,
                    HttpMethod.GET,
                    new HttpEntity<>(createHeaders()),
                    JsonNode.class
            );
            return response.getBody();
        } catch (RestClientException e) {
            log.error("Get WR {} failed: {}", wrId, e.getMessage());
            throw e;
        }
    }

    @Override
    public JsonNode orchestrate(String wrId) {
        try {
            ResponseEntity<JsonNode> response = restTemplate.exchange(
                    baseUrl + "/work-requests/" + wrId + "/orchestrate",
                    HttpMethod.POST,
                    new HttpEntity<>(createHeaders()),
                    JsonNode.class
            );
            return response.getBody();
        } catch (RestClientException e) {
            log.error("Orchestrate WR {} failed: {}", wrId, e.getMessage());
            throw e;
        }
    }

    private HttpHeaders createHeaders() {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return headers;
    }
}
