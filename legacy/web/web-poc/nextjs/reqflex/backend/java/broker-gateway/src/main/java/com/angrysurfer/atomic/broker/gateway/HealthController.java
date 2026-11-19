package com.angrysurfer.atomic.broker.gateway;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

@RestController
public class HealthController {

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        Map<String, Object> healthStatus = new HashMap<>();
        
        try {
            // Basic health check - you can add more sophisticated checks here
            // like database connectivity, external service availability, etc.
            
            healthStatus.put("status", "UP");
            healthStatus.put("service", "spring-broker-gateway");
            healthStatus.put("timestamp", Instant.now().toString());

            Map<String, Object> details = new HashMap<>();
            details.put("application", "BrokerGatewayApplication");
            details.put("version", "1.0.0");
            healthStatus.put("details", details);

            return ResponseEntity.ok(healthStatus);

        } catch (Exception e) {
            healthStatus.put("status", "DOWN");
            healthStatus.put("service", "spring-broker-gateway");
            healthStatus.put("timestamp", Instant.now().toString());
            healthStatus.put("error", e.getMessage());
            
            return ResponseEntity.status(503).body(healthStatus);
        }
    }
}