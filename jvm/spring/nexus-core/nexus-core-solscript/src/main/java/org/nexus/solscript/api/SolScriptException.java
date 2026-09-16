package org.nexus.solscript.api;

import org.springframework.http.HttpStatus;

/** Typed SOLScript facade error — rendered as the SolScriptError contract body. */
public class SolScriptException extends RuntimeException {
    public final HttpStatus status;
    public final String code;
    public final boolean retryable;

    public SolScriptException(HttpStatus status, String code, String message, boolean retryable) {
        super(message);
        this.status = status;
        this.code = code;
        this.retryable = retryable;
    }
}