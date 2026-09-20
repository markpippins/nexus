package org.nexus.core.wrp;

/** WRP kernel failure (invalid delta, unknown state, identity conflict). */
public final class WrpException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    public WrpException(String message) { super(message); }
}
