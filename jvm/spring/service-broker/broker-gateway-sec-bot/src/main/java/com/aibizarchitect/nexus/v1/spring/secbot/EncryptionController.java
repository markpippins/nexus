package com.aibizarchitect.nexus.v1.spring.secbot;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.aibizarchitect.nexus.v1.broker.api.ServiceRequest;
import com.aibizarchitect.nexus.v1.broker.api.ServiceResponse;

@RestController
@RequestMapping("/secbot")
public class EncryptionController {

    private final EncryptionService encryptionService;

    // Per architect remediation F4: require API key for encrypt/decrypt endpoints
    // API key must be provided via X-API-Key header
    private static final String API_KEY_HEADER = "X-API-Key";
    private static final String EXPECTED_API_KEY = System.getenv("SECBOT_API_KEY");

    public EncryptionController(EncryptionService encryptionService) {
        this.encryptionService = encryptionService;
    }

    @PostMapping("/encrypt")
    public ResponseEntity<ServiceResponse> encrypt(HttpServletRequest request, @RequestBody ServiceResponse serviceResponse) {
        if (!isAuthorized(request)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        return ResponseEntity.ok(encryptionService.encrypt(serviceResponse));
    }

    @PostMapping("/decrypt")
    public ResponseEntity<ServiceRequest> decrypt(HttpServletRequest request, @RequestBody ServiceRequest serviceRequest) {
        if (!isAuthorized(request)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        return ResponseEntity.ok(encryptionService.decrypt(serviceRequest));
    }

    private boolean isAuthorized(HttpServletRequest request) {
        if (EXPECTED_API_KEY == null || EXPECTED_API_KEY.isEmpty()) {
            // Fail-closed: no API key configured = no access
            return false;
        }
        String apiKey = request.getHeader(API_KEY_HEADER);
        return EXPECTED_API_KEY.equals(apiKey);
    }
}