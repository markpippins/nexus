package com.angrysurfer.atomic.broker.api;

import java.time.Instant;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import java.io.Serializable;

public class ServiceResponse<T> implements Serializable {

    private static final long serialVersionUID = 1L;

    private boolean ok;
    private T data;
    private List<Map<String, Object>> errors;                   
    private String requestId;
    private Instant ts;

    private String version = "1.0";

    private String service;

    private String operation;

    private boolean encrypt = false;

    public ServiceResponse() {
    }

    public ServiceResponse(boolean ok, T data, List<Map<String, Object>> errors, String requestId, Instant ts) {
        this.ok = ok;
        this.data = data;
        this.errors = errors;
        this.requestId = requestId;
        this.ts = ts;
    }

    public ServiceResponse(String service, String operation, boolean ok, T data, List<Map<String, Object>> errors, String requestId, Instant ts) {
        this(ok, data, errors, requestId, ts);
        this.service = service;
        this.operation = operation;
    }

    public static <T> ServiceResponse<T> ok(String service, String operation, T data, String requestId) {
        return new ServiceResponse<>(service, operation, true, data, Collections.emptyList(), requestId, Instant.now());
    }

    public static <T> ServiceResponse<T> ok(T data, String requestId) {
        return new ServiceResponse<>(true, data, Collections.emptyList(), requestId, Instant.now());
    }

    public static ServiceResponse<?> error(String service, String operation, List<Map<String, Object>> errors, String requestId) {
        return new ServiceResponse<>(service, operation, false, null, errors, requestId, Instant.now());
    }

    public static ServiceResponse<?> error(List<Map<String, Object>> errors, String requestId) {
        return new ServiceResponse<>(false, null, errors, requestId, Instant.now());
    }

    public boolean isOk() {
        return ok;
    }

    public void setOk(boolean ok) {
        this.ok = ok;
    }

    public void setData(T data) {
        this.data = data;
    }

    public void setErrors(List<Map<String, Object>> errors) {
        this.errors = errors;
    }

    public void setRequestId(String requestId) {
        this.requestId = requestId;
    }

    public void setTs(Instant ts) {
        this.ts = ts;
    }

    public T getData() {
        return data;
    }

    public List<Map<String, Object>> getErrors() {
        return errors;
    }

    public String getRequestId() {
        return requestId;
    }

    public Instant getTs() {
        return ts;
    }

    public String getVersion() {
        return version;
    }

    public void setVersion(String version) {
        this.version = version;
    }

    public String getService() {
        return service;
    }

    public void setService(String service) {
        this.service = service;
    }

    public String getOperation() {
        return operation;
    }

    public void setOperation(String operation) {
        this.operation = operation;
    }

    public boolean isEncrypt() {
        return encrypt;
    }

    public void setEncrypt(boolean encrypt) {
        this.encrypt = encrypt;
    }

    public void addError(String field, String message) {
        if (this.errors == null) {
            this.errors = new java.util.ArrayList<>();
        }
        this.errors.add(Map.of("field", field, "message", message));
        this.ok = false;
    }
}
