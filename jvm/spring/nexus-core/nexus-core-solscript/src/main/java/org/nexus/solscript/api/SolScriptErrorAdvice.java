package org.nexus.solscript.api;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.LinkedHashMap;
import java.util.Map;

/** Renders SolScriptException as the SolScriptError contract body. */
@RestControllerAdvice
public class SolScriptErrorAdvice {

    @ExceptionHandler(SolScriptException.class)
    public ResponseEntity<Map<String, Object>> handle(SolScriptException ex) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("code", ex.code);
        body.put("message", ex.getMessage());
        body.put("retryable", ex.retryable);
        body.put("service", "solscript-java");
        return ResponseEntity.status(ex.status).body(body);
    }
}