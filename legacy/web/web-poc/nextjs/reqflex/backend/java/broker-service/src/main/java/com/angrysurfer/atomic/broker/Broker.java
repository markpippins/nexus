package com.angrysurfer.atomic.broker;

import java.lang.annotation.Annotation;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Parameter;
import java.security.Principal;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationContext;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import com.angrysurfer.atomic.broker.api.ServiceRequest;
import com.angrysurfer.atomic.broker.api.ServiceResponse;
import com.angrysurfer.atomic.broker.spi.BrokerOperation;
import com.angrysurfer.atomic.broker.spi.BrokerParam;
import com.fasterxml.jackson.databind.ObjectMapper;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validator;

@Component
public class Broker {

    private static final Logger log = LoggerFactory.getLogger(Broker.class);

    private final ApplicationContext ctx;
    private final ObjectMapper objectMapper;
    private final Validator validator;

    private record MethodKey(String service, String operation) {

    }

    private final Map<MethodKey, Method> methodCache = new ConcurrentHashMap<>();

    public Broker(ApplicationContext ctx, ObjectMapper objectMapper, Validator validator) {
        this.ctx = ctx;
        this.objectMapper = objectMapper;
        this.validator = validator;
        log.info("ServiceBroker initialized");
    }

    public ServiceResponse<?> submit(ServiceRequest req) {
        log.info("Invoking service: {}, operation: {}, requestId: {}", req.getService(), req.getOperation(),
                req.getRequestId());
        try {
            Object bean = resolveBean(req.getService());
            Method method = resolveMethod(bean, req.getOperation());
            Object[] args = bindArgs(method, req.getParams(), req.getRequestId());

            Object result = method.invoke(bean, args);

            // If a service method tries to return ServiceResponse, unwrap it
            if (result instanceof ServiceResponse<?> re) {
                log.debug("Service returned ServiceResponse directly, unwrapping body");
                if (re.isOk()) {
                    return ServiceResponse.ok(req.getService(), req.getOperation(), re.getData(), req.getRequestId());
                } else {
                    return ServiceResponse.error(req.getService(), req.getOperation(), re.getErrors(),
                            req.getRequestId());
                }
            }

            log.debug("Service returned: {}", result);
            return ServiceResponse.ok(req.getService(), req.getOperation(), result, req.getRequestId());

        } catch (NoSuchElementException e) {
            log.warn("Not found: {}", e.getMessage());
            return ServiceResponse.error(req.getService(), req.getOperation(),
                    List.of(Map.of("code", "not_found", "message", e.getMessage())), req.getRequestId());

        } catch (BrokerValidationException e) {
            log.warn("Validation error: {}", e.getErrors());
            return ServiceResponse.error(req.getService(), req.getOperation(), e.getErrors(), e.getRequestId());

        } catch (IllegalArgumentException e) {
            log.warn("Binding error: {}", e.getMessage());
            return ServiceResponse.error(req.getService(), req.getOperation(),
                    List.of(Map.of("code", "binding_error", "message", e.getMessage())), req.getRequestId());

        } catch (InvocationTargetException e) {
            Throwable cause = e.getTargetException();
            log.error("Service error: {}", cause.getMessage(), cause);
            return ServiceResponse.error(req.getService(), req.getOperation(), List.of(Map.of("code", "service_error",
                    "type", cause.getClass().getSimpleName(), "message", cause.getMessage())), req.getRequestId());

        } catch (IllegalAccessException | RuntimeException e) {
            log.error("Broker error: {}", e.getMessage(), e);
            return ServiceResponse.error(req.getService(), req.getOperation(),
                    List.of(Map.of("code", "broker_error", "message", e.getMessage())), req.getRequestId());
        }
    }

    private Object resolveBean(String serviceName) {
        if (!StringUtils.hasText(serviceName)) {
            log.error("Missing 'service' name");
            throw new NoSuchElementException("Missing 'service' name.");
        }
        if (ctx.containsBean(serviceName)) {
            log.debug("Found bean by name: {}", serviceName);
            return ctx.getBean(serviceName);
        }
        // Try by type simple name (e.g., "UserService" -> bean)
        String[] beanNames = ctx.getBeanDefinitionNames();
        for (String name : beanNames) {
            Object bean = ctx.getBean(name);
            if (bean.getClass().getSimpleName().equalsIgnoreCase(serviceName)) {
                log.debug("Found bean by type simple name: {}", serviceName);
                return bean;
            }
        }
        log.error("Service bean not found: {}", serviceName);
        throw new NoSuchElementException("Service bean not found: " + serviceName);
    }

    private Method resolveMethod(Object bean, String operation) {
        if (!StringUtils.hasText(operation)) {
            log.error("Missing 'operation' name");
            throw new NoSuchElementException("Missing 'operation' name.");
        }
        MethodKey key = new MethodKey(bean.getClass().getName(), operation);
        Method cached = methodCache.get(key);
        if (cached != null) {
            log.debug("Using cached method for operation: {}", operation);
            return cached;
        }

        // Only methods annotated with @BrokerOperation are exposed.
        List<Method> candidates = Arrays.stream(bean.getClass().getMethods())
                .filter(m -> m.isAnnotationPresent(BrokerOperation.class)).filter(m -> {
                    String alias = m.getAnnotation(BrokerOperation.class).value();
                    return operation.equals(alias) || operation.equals(m.getName());
                }).toList();

        if (candidates.isEmpty()) {
            log.error("Operation not exposed: {}", operation);
            throw new NoSuchElementException("Operation not exposed: " + operation);
        }
        if (candidates.size() > 1) {
            // Prefer exact alias match
            Optional<Method> exact = candidates.stream()
                    .filter(m -> operation.equals(m.getAnnotation(BrokerOperation.class).value())).findFirst();
            Method chosen = exact.orElse(candidates.get(0));
            methodCache.put(key, chosen);
            log.debug("Multiple candidates, using: {}", chosen.getName());
            return chosen;
        }
        Method method = candidates.get(0);
        methodCache.put(key, method);
        log.debug("Resolved method: {}", method.getName());
        return method;
    }

    private Object[] bindArgs(Method method, Map<String, Object> params, String requestId) {
        params = (params == null) ? Map.of() : params;
        Parameter[] ps = method.getParameters();
        Object[] bound = new Object[ps.length];

        List<Map<String, Object>> violations = new ArrayList<>();

        for (int i = 0; i < ps.length; i++) {
            Parameter p = ps[i];
            Class<?> pt = p.getType();

            // Allow context injection without being in params:
            if (Principal.class.isAssignableFrom(pt)) {
                bound[i] = null; // Let a HandlerMethodArgumentResolver variant inject if you extend; keep null
                                 // for now.
                continue;
            }
            if (jakarta.servlet.http.HttpServletRequest.class.isAssignableFrom(pt)) {
                bound[i] = null;
                continue;
            }

            // Name to lookup in params:
            BrokerParam paramAnnotation = p.getAnnotation(BrokerParam.class);
            String name = (paramAnnotation != null
                    && org.springframework.util.StringUtils.hasText(paramAnnotation.value())) ? paramAnnotation.value()
                            : p.getName(); // requires -parameters at compile to retain names

            boolean required = true; // (paramAnnotation == null) || paramAnnotation.required();

            Object raw = params.get(name);
            if (raw == null && required) {
                log.warn("Missing required parameter: {}", name);
                throw new IllegalArgumentException("Missing required parameter: " + name);
            }

            Object converted = objectMapper.convertValue(raw, objectMapper.constructType(p.getParameterizedType()));

            // Manual validation for @Valid parameters (method-level programmatic):
            if (hasAnnotation(p, jakarta.validation.Valid.class) && converted != null) {
                Set<ConstraintViolation<Object>> errs = validator.validate(converted);
                if (!errs.isEmpty()) {
                    errs.stream().map(cv -> Map.<String, Object>of("param", name, "path",
                            cv.getPropertyPath().toString(), "msg", cv.getMessage())).forEach(violations::add);
                }
            }

            bound[i] = converted;
        }

        if (!violations.isEmpty()) {
            log.warn("Validation violations: {}", violations);
            throw new BrokerValidationException(violations, requestId);
        }
        return bound;
    }

    private boolean hasAnnotation(Parameter p, Class<? extends Annotation> a) {
        for (Annotation an : p.getAnnotations()) {
            if (an.annotationType().equals(a)) {
                return true;
            }
        }
        return false;
    }

    private ServiceResponse<?> badRequest(String code, String msg, String requestId) {
        log.warn("Bad request [{}]: {}", code, msg);
        return ServiceResponse.error(List.of(Map.of("code", code, "message", msg)), requestId);
    }

    private ServiceResponse<?> serverError(String code, String msg, String requestId) {
        log.error("Server error [{}]: {}", code, msg);
        return ServiceResponse.error(List.of(Map.of("code", code, "message", msg)), requestId);
    }

}
