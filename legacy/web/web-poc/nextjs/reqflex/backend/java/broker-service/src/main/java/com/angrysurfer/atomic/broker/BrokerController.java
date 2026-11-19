package com.angrysurfer.atomic.broker;

import java.util.Collections;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

import com.angrysurfer.atomic.broker.api.ServiceRequest;
import com.angrysurfer.atomic.broker.api.ServiceResponse;
import com.angrysurfer.atomic.admin.logging.service.AdminLoggingService;

@CrossOrigin(originPatterns = "*", allowedHeaders = "*", allowCredentials = "true", methods = {RequestMethod.GET, RequestMethod.POST, RequestMethod.PUT, RequestMethod.DELETE, RequestMethod.OPTIONS})
@RestController
@RequestMapping("/api/broker")
public class BrokerController {

    private static final Logger log = LoggerFactory.getLogger(BrokerController.class);

    private final Broker broker;
    private final AdminLoggingService adminLoggingService;

    public BrokerController(Broker broker, AdminLoggingService adminLoggingService) {
        this.broker = broker;
        this.adminLoggingService = adminLoggingService;
        log.info("BrokerController initialized");
    }

    @PostMapping(value = "/testBroker")
    public ResponseEntity<?> testBroker() {
        // log.debug("Received request: {}", request);

        ServiceRequest request = new ServiceRequest("testBroker", "test",
                Collections.emptyMap(), "test-request");

        ServiceResponse<?> response = broker.submit(request);

        if (response.isOk()) {
            return ResponseEntity.ok(response);
        } else {
            // decide on HTTP code: validation errors = 400, not_found = 404, etc.
            // simplest case: always return 400 for errors
            return ResponseEntity.badRequest().body(response);
        }
    }

    @PostMapping(value = "/submitRequest", consumes = {"application/json"})
    public ResponseEntity<?> submitRequest(@RequestBody ServiceRequest request) {
        log.debug("Received request: {}", request);

        // Log the request before processing it
        UUID logId = null;
        String userId = extractUserId(request); // Extract user ID or set to a default
        try {
            var logEntry = adminLoggingService.logRequest(request, userId);
            if (logEntry != null) {
                logId = logEntry.getId();
            }
        } catch (Exception e) {
            log.error("Error logging request: {}", e.getMessage(), e);
        }

        ServiceResponse<?> response = broker.submit(request);
        
        // Update the log entry with success/failure status
        if (logId != null) {
            adminLoggingService.updateLogEntry(logId, response.isOk(), 
                response.isOk() ? null : extractErrorMessage(response));
        }

        log.debug("returning: {}", response);

        if (response.isOk()) {
            return ResponseEntity.ok(response);
        } else {
            // decide on HTTP code: validation errors = 400, not_found = 404, etc.
            // simplest case: always return 400 for errors
            return ResponseEntity.badRequest().body(response);
        }
    }

    private String extractUserId(ServiceRequest request) {
        // Extract userId from request. This could come from a header, or be extracted from security context
        // For now, using a default value, but in a real application, this would come from authentication
        return "anonymous"; // Replace with actual user extraction logic
    }

    private String extractErrorMessage(ServiceResponse<?> response) {
        // Extract error message from response, assuming error details are stored in response
        if (response.getErrors() != null && !response.getErrors().isEmpty()) {
            // Extract the first error message or return all errors as JSON
            return response.getErrors().toString();
        }
        return "Unknown error occurred";
    }
}
