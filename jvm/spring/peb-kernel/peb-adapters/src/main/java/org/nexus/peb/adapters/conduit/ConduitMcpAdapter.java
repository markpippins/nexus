package org.nexus.peb.adapters.conduit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.nexus.peb.domain.port.ConduitMcpPort;
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
 * REST client adapter for conduit-mcp (port 3100).
 * 
 * Conduit-mcp is the receipt-first authority for the pipeline system.
 * This adapter provides the write path for PEB → conduit integration:
 * - Submit work requests
 * - Query work request state
 * - Record governance events as receipts
 */
@Component
public class ConduitMcpAdapter implements ConduitMcpPort {

    private static final Logger log = LoggerFactory.getLogger(ConduitMcpAdapter.class);

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;
    private final String baseUrl;

    public ConduitMcpAdapter(
            @Value("${peb.conduit.url:http://localhost:3100}") String baseUrl,
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
    public JsonNode submitWorkRequest(JsonNode workRequest) {
        return post("/wr/submit", workRequest);
    }

    @Override
    public JsonNode getWorkRequest(String wrId) {
        return get("/wr/" + wrId);
    }

    @Override
    public JsonNode transitionWorkRequest(String wrId, JsonNode transition) {
        return post("/wr/" + wrId + "/transition", transition);
    }

    @Override
    public JsonNode issueReceipt(JsonNode receipt) {
        return post("/vision/receipts", receipt);
    }

    @Override
    public JsonNode queryState() {
        return get("/state");
    }

    private JsonNode get(String path) {
        try {
            ResponseEntity<JsonNode> response = restTemplate.exchange(
                    baseUrl + path,
                    HttpMethod.GET,
                    new HttpEntity<>(createHeaders()),
                    JsonNode.class
            );
            return response.getBody();
        } catch (RestClientException e) {
            log.error("GET {} failed: {}", path, e.getMessage());
            throw e;
        }
    }

    private JsonNode post(String path, JsonNode body) {
        try {
            ResponseEntity<JsonNode> response = restTemplate.exchange(
                    baseUrl + path,
                    HttpMethod.POST,
                    new HttpEntity<>(body, createHeaders()),
                    JsonNode.class
            );
            return response.getBody();
        } catch (RestClientException e) {
            log.error("POST {} failed: {}", path, e.getMessage());
            throw e;
        }
    }

    private HttpHeaders createHeaders() {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return headers;
    }
}
