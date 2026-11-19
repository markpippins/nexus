package com.angrysurfer.atomic.broker;

import java.util.List;
import java.util.Map;

public class BrokerValidationException extends IllegalArgumentException {
    private final List<Map<String, Object>> errors;
    private final String requestId;

    public BrokerValidationException(List<Map<String, Object>> errors, String requestId) {
        super("Validation failed");
        this.errors = errors;
        this.requestId = requestId;
    }

    public List<Map<String, Object>> getErrors() { return errors; }

    public String getRequestId() { return requestId; }
}