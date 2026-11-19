package com.angrysurfer.atomic.broker.api;

import java.io.Serializable;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import lombok.Data;

@Data
public class ServiceRegistration implements Serializable {
    
    private static final long serialVersionUID = 1L;

    @NotBlank(message = "Service name is required")
    private String serviceName;
    
    @NotEmpty(message = "At least one operation is required")
    private List<String> operations;
    
    @NotBlank(message = "Endpoint URL is required")
    private String endpoint;
    
    @NotBlank(message = "Health check URL is required")
    private String healthCheck;
    
    private Map<String, Object> metadata;
    
    private Instant lastHeartbeat;
    
    private ServiceStatus status;
    
    public enum ServiceStatus {
        HEALTHY, UNHEALTHY, UNKNOWN
    }
}
