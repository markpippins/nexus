package com.angrysurfer.atomic.broker;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.ApplicationContext;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

import com.angrysurfer.atomic.broker.api.ServiceRegistration;
import com.angrysurfer.atomic.broker.api.ServiceRequest;
import com.angrysurfer.atomic.broker.api.ServiceResponse;
import com.angrysurfer.atomic.broker.spi.BrokerOperation;

@Component
public class BrokerAutoRegistration {

    private static final Logger log = LoggerFactory.getLogger(BrokerAutoRegistration.class);

    private final ApplicationContext context;
    private final Broker broker;
    private RemoteBrokerClient remoteBrokerClient;

    public BrokerAutoRegistration(ApplicationContext context, Broker broker) {
        this.context = context;
        this.broker = broker;
    }

    @Autowired(required = false)
    public void setRemoteBrokerClient(RemoteBrokerClient remoteBrokerClient) {
        this.remoteBrokerClient = remoteBrokerClient;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void registerServices() {
        log.info("Starting auto-registration of broker services...");

        String[] beanNames = context.getBeanDefinitionNames();
        Map<String, List<String>> serviceOperations = new HashMap<>();

        for (String beanName : beanNames) {
            Object bean = context.getBean(beanName);
            Class<?> beanClass = bean.getClass();
            
            // Skip Spring internal beans and proxies if possible, but we need to check methods
            // AOP proxies might hide annotations, but Spring usually handles this.
            
            List<String> operations = new ArrayList<>();
            for (Method method : beanClass.getMethods()) {
                if (method.isAnnotationPresent(BrokerOperation.class)) {
                    BrokerOperation annotation = method.getAnnotation(BrokerOperation.class);
                    String opName = annotation.value();
                    if (opName.isEmpty()) {
                        opName = method.getName();
                    }
                    operations.add(opName);
                }
            }

            if (!operations.isEmpty()) {
                // Use the bean name or a dedicated service name if we had an annotation for it
                // For now, using bean name as service name
                serviceOperations.put(beanName, operations);
            }
        }

        for (Map.Entry<String, List<String>> entry : serviceOperations.entrySet()) {
            String serviceName = entry.getKey();
            List<String> operations = entry.getValue();

            // Skip registering the registry itself to avoid loops, or handle it gracefully
            // The registry needs to be up to receive registrations.
            // Since this is in-process, it should be fine.

            ServiceRegistration registration = new ServiceRegistration();
            registration.setServiceName(serviceName);
            registration.setOperations(operations);
            registration.setEndpoint("local"); // Since it's a monolith
            registration.setHealthCheck("local");
            registration.setStatus(ServiceRegistration.ServiceStatus.HEALTHY);

            try {
                // Use either local broker or remote broker based on configuration
                // If remote broker is configured, use it for registration
                if (remoteBrokerClient != null && remoteBrokerClient.isRemoteConfigured()) {
                    log.debug("Using remote broker for service registration: {}", serviceName);
                    Map<String, Object> params = new HashMap<>();
                    params.put("registration", registration);

                    ServiceRequest request = new ServiceRequest("serviceRegistry", "register", params, "auto-reg-" + serviceName);
                    ServiceResponse<?> response = remoteBrokerClient.submit(request);

                    if (response.isOk()) {
                        log.info("Successfully auto-registered service with remote broker: {}", serviceName);
                    } else {
                        log.warn("Failed to auto-registered service with remote broker: {} - Error: {}",
                                serviceName, response.getErrors());
                    }
                } else {
                    log.debug("Using local broker for service registration: {}", serviceName);
                    // Use the local Broker to send the registration request
                    // This assumes "serviceRegistry" bean has "register" operation
                    Map<String, Object> params = new HashMap<>();
                    params.put("registration", registration);

                    ServiceRequest request = new ServiceRequest("serviceRegistry", "register", params, "auto-reg-" + serviceName);
                    broker.submit(request);

                    log.info("Auto-registered service with local broker: {}", serviceName);
                }
            } catch (Exception e) {
                log.error("Failed to auto-register service: {}", serviceName, e);
            }
        }
        
        log.info("Auto-registration complete.");
    }
}
