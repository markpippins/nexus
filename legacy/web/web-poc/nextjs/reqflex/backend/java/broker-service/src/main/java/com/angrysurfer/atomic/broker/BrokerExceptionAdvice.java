package com.angrysurfer.atomic.broker;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import com.angrysurfer.atomic.broker.api.ServiceResponse;

@RestControllerAdvice
public class BrokerExceptionAdvice {

    /**
     * Handles validation errors for standard Spring MVC data binding on @RequestBody.
     */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<?> onInvalid(MethodArgumentNotValidException e) {
        List<Map<String, Object>> errs = e.getBindingResult().getFieldErrors().stream()
                .map(fe -> {
                    Map<String, Object> errorMap = new HashMap<>();
                    errorMap.put("code", "invalid");
                    errorMap.put("field", fe.getField());
                    errorMap.put("message", fe.getDefaultMessage());
                    return errorMap;
                })
                .collect(Collectors.toList());
        return ResponseEntity.badRequest().body(ServiceResponse.error(errs, null));
    }

    @ExceptionHandler(BrokerValidationException.class)
    public ResponseEntity<?> onBrokerValidation(BrokerValidationException e) {
        return ResponseEntity.badRequest().body(ServiceResponse.error(e.getErrors(), e.getRequestId()));
    }
}