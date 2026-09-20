package org.nexus.core.meep;

/** Kernel-level failure (frozen-contract violations, cycle detection, unknown event types). */
public final class MeepException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    public MeepException(String message) { super(message); }
}
