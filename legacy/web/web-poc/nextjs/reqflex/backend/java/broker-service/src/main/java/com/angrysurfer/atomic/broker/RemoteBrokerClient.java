package com.angrysurfer.atomic.broker;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import com.angrysurfer.atomic.broker.api.ServiceRequest;
import com.angrysurfer.atomic.broker.api.ServiceResponse;

/**
 * A client for connecting to a remote broker gateway instead of using local broker.
 * This is used when services are configured to register with a central broker gateway.
 */
@Component
@ConditionalOnProperty(name = "broker.remote.gateway.url")
public class RemoteBrokerClient {

    private static final Logger log = LoggerFactory.getLogger(RemoteBrokerClient.class);

    private final String remoteBrokerUrl;
    private final RestTemplate restTemplate;

    public RemoteBrokerClient(
            @Value("${broker.remote.gateway.url:}") String remoteBrokerUrl) {
        this.remoteBrokerUrl = remoteBrokerUrl;

        // Create our own RestTemplate instance to avoid conflicts with other configurations
        this.restTemplate = new RestTemplate();
        // Configure timeouts
        if (this.restTemplate.getRequestFactory() instanceof SimpleClientHttpRequestFactory) {
            SimpleClientHttpRequestFactory factory = (SimpleClientHttpRequestFactory) this.restTemplate.getRequestFactory();
            factory.setConnectTimeout(10000); // 10 seconds
            factory.setReadTimeout(30000);    // 30 seconds
        } else {
            // Set a new factory with timeouts
            SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
            factory.setConnectTimeout(10000);
            factory.setReadTimeout(30000);
            this.restTemplate.setRequestFactory(factory);
        }
        
        if (remoteBrokerUrl != null && !remoteBrokerUrl.trim().isEmpty()) {
            log.info("Remote broker client configured to connect to: {}", remoteBrokerUrl);
        } else {
            log.info("Remote broker client not configured (broker.remote.gateway.url not set)");
        }
    }

    public ServiceResponse<?> submit(ServiceRequest request) {
        if (remoteBrokerUrl == null || remoteBrokerUrl.trim().isEmpty()) {
            log.warn("Remote broker URL not configured, cannot submit request");
            return ServiceResponse.error(
                request.getService(), 
                request.getOperation(), 
                java.util.List.of(java.util.Map.of("code", "remote_broker_not_configured", "message", "Remote broker URL not configured")), 
                request.getRequestId()
            );
        }

        // Construct the URL for the remote broker endpoint
        String fullUrl = remoteBrokerUrl + "/api/broker/submitRequest";
        
        try {
            log.debug("Submitting request to remote broker: {} -> {}", 
                     fullUrl, request.getService() + "." + request.getOperation());
            
            ServiceResponse<?> response = restTemplate.postForObject(fullUrl, request, ServiceResponse.class);
            
            if (response != null) {
                log.debug("Received response from remote broker: {}", response.isOk());
                return response;
            } else {
                log.warn("Received null response from remote broker");
                return ServiceResponse.error(
                    request.getService(), 
                    request.getOperation(), 
                    java.util.List.of(java.util.Map.of("code", "null_response", "message", "Remote broker returned null response")), 
                    request.getRequestId()
                );
            }
        } catch (Exception e) {
            log.error("Error submitting request to remote broker: {}", e.getMessage(), e);
            return ServiceResponse.error(
                request.getService(), 
                request.getOperation(), 
                java.util.List.of(java.util.Map.of("code", "remote_broker_error", "message", e.getMessage())), 
                request.getRequestId()
            );
        }
    }

    public boolean isRemoteConfigured() {
        return remoteBrokerUrl != null && !remoteBrokerUrl.trim().isEmpty();
    }
}