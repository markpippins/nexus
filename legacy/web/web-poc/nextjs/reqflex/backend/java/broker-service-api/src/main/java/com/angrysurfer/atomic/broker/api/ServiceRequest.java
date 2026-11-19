package com.angrysurfer.atomic.broker.api;

import java.util.Map;

import java.io.Serializable;

public class ServiceRequest implements Serializable {

    private static final long serialVersionUID = 1L;
    private String service;
    private String operation;
    private Map<String, Object> params;
    private String requestId;

    private boolean encrypt = false;

    public ServiceRequest() {
    }

    public ServiceRequest(String service, String operation, Map<String, Object> params, String requestId) {
        this.service = service;
        this.operation = operation;
        this.params = params;
        this.requestId = requestId;
    }

    public String getService() {
        return service;
    }

    public String getOperation() {
        return operation;
    }

    public Map<String, Object> getParams() {
        return params;
    }

    public String getRequestId() {
        return requestId;
    }

    public boolean isEncrypt() {
        return encrypt;
    }

    public void setEncrypt(boolean encrypt) {
        this.encrypt = encrypt;
    }

    public void setParams(Map<String, Object> params) {
        this.params = params;
    }
}