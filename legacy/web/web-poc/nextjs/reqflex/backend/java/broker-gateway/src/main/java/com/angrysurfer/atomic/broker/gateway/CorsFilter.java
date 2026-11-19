package com.angrysurfer.atomic.broker.gateway;

import java.io.IOException;

import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import org.springframework.stereotype.Component;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class CorsFilter implements Filter {

    private static final Logger log = LoggerFactory.getLogger(CorsFilter.class);

    @Override
    public void doFilter(ServletRequest req, ServletResponse res, FilterChain chain)
            throws IOException, ServletException {

        HttpServletResponse response = (HttpServletResponse) res;
        HttpServletRequest request = (HttpServletRequest) req;

        String origin = request.getHeader("Origin");
        String requestUri = request.getRequestURI();
        String method = request.getMethod();

        log.info("CORS Filter - Method: {}, URI: {}, Origin: {}", method, requestUri, origin);

        // For development - allow specific origins with credentials
        if (origin != null && !origin.isEmpty()) {
            // Check if origin is a trusted development origin
            if (isTrustedOrigin(origin)) {
                response.setHeader("Access-Control-Allow-Origin", origin);
                response.setHeader("Access-Control-Allow-Credentials", "true");
            } else {
                // For untrusted origins, use wildcard but without credentials
                response.setHeader("Access-Control-Allow-Origin", "*");
            }
        } else {
            response.setHeader("Access-Control-Allow-Origin", "*");
        }

        response.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD, TRACE");
        response.setHeader("Access-Control-Allow-Headers",
                "Origin, X-Requested-With, Content-Type, Accept, Authorization, X-CSRF-TOKEN");
        response.setHeader("Access-Control-Expose-Headers",
                "Authorization, Content-Type, X-Requested-With, Link, X-Total-Count");
        response.setHeader("Vary", "Origin");

        if ("OPTIONS".equalsIgnoreCase(method)) {
            log.info("CORS Filter - Handling preflight OPTIONS request from origin: {}", origin);
            response.setStatus(HttpServletResponse.SC_OK);
        } else {
            chain.doFilter(req, res);
        }
    }

    @org.springframework.beans.factory.annotation.Value("${allowed.origins}")
    private String[] allowedOrigins;

    private boolean isTrustedOrigin(String origin) {
        if (allowedOrigins == null || allowedOrigins.length == 0) {
            return false;
        }
        for (String allowed : allowedOrigins) {
            allowed = allowed.trim();
            if (allowed.equals("*")) {
                return true;
            }
            // Handle wildcards
            if (allowed.contains("*")) {
                String regex = "^" + allowed.replace(".", "\\.").replace("*", ".*") + "$";
                if (origin.matches(regex)) {
                    return true;
                }
            } else if (origin.equals(allowed)) {
                return true;
            }
        }
        return false;
    }
}
