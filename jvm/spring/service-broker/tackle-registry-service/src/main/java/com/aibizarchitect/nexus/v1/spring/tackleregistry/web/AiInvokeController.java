package com.aibizarchitect.nexus.v1.spring.tackleregistry.web;

import com.aibizarchitect.nexus.v1.spring.tackleregistry.tackle.InferenceService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * HTTP inference driven by the tackle registry — the capability that makes
 * losm-host-service worth existing: resolve a role's active config bundle
 * (same data tackle-srv manages) and run a prompt through it with Spring AI.
 *
 *   POST /ai/invoke {"role":"engineer","prompt":"..."}
 *   POST /ai/invoke {"provider_type":"ollama","endpoint_url":"http://...",
 *                    "model_identifier":"llama3","prompt":"..."}   (direct)
 */
/**
 * DORMANCY GUARD (issue 59bcd3da, item 4): registered ONLY when
 * app.ai-invoke.enabled=true. Default (absent) = DORMANT — the route
 * does not exist. The direct mode forwards a caller-supplied
 * endpoint_url + apiKey server-side (SSRF-shaped) and the surface is
 * unauthenticated; dormancy was previously accidental. Enabling is a
 * deliberate, auditable act and should ride the broker auth-provider
 * work before going live.
 */
@RestController
@ConditionalOnProperty(name = "app.ai-invoke.enabled", havingValue = "true")
@RequestMapping("/ai")
public class AiInvokeController {

    private final InferenceService inference;

    public AiInvokeController(InferenceService inference) {
        this.inference = inference;
    }

    /** Field naming matches tackle-srv's snake_case API surface.
     *  NOTE: Boot 4 ships Jackson 3 (tools.jackson) — com.fasterxml
     *  annotations are silently ignored by the HTTP message converters. */
    @tools.jackson.databind.annotation.JsonNaming(
            tools.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record InvokeRequest(
            String role, String providerType, String endpointUrl,
            String apiKey, String modelIdentifier, String prompt) {}

    @PostMapping("/invoke")
    public ResponseEntity<Map<String, Object>> invoke(@RequestBody InvokeRequest req) {
        if (req.prompt() == null || req.prompt().isBlank()) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "prompt is required"));
        }
        try {
            String reply = (req.role() != null && !req.role().isBlank())
                    ? inference.invokeForRole(req.role(), req.prompt())
                    : inference.invoke(req.providerType(), req.endpointUrl(),
                            req.apiKey(), req.modelIdentifier(), req.prompt());
            return ResponseEntity.ok(Map.of("reply", reply));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(404).body(Map.of("error", e.getMessage()));
        } catch (InferenceService.UnsupportedProviderException e) {
            return ResponseEntity.status(415).body(Map.of("error", e.getMessage()));
        } catch (Exception e) {
            org.slf4j.LoggerFactory.getLogger(AiInvokeController.class)
                    .error("inference failed", e);
            return ResponseEntity.status(502)
                    .body(Map.of("error", "inference failed: " + e.getMessage()));
        }
    }

    /** Which provider types this service can serve over HTTP. */
    @GetMapping("/invoke/capabilities")
    public Map<String, Object> capabilities() {
        return Map.of(
                "http_invocable", new String[]{"openai", "deepseek", "google", "ollama"},
                "note", "CLI-harness providers (opencode, codex...) remain tackle-srv territory");
    }
}
