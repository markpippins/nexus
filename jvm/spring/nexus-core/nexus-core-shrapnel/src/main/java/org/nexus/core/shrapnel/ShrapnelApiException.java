package org.nexus.core.shrapnel;

/**
 * Port of the TS service's {@code ApiError} ({@code src/errors.js}) — carries
 * the HTTP status so the controller advice maps it to the same JSON envelope:
 * {@code {"error": {"message": ..., "details": ...}}}.
 */
public class ShrapnelApiException extends RuntimeException {

    private final int status;
    private final Object details;

    public ShrapnelApiException(int status, String message) {
        this(status, message, null);
    }

    public ShrapnelApiException(int status, String message, Object details) {
        super(message);
        this.status = status;
        this.details = details;
    }

    public int getStatus() {
        return status;
    }

    public Object getDetails() {
        return details;
    }

    public static ShrapnelApiException badRequest(String message) {
        return new ShrapnelApiException(400, message);
    }

    public static ShrapnelApiException notFound(String message) {
        return new ShrapnelApiException(404, message == null ? "not found" : message);
    }

    public static ShrapnelApiException conflict(String message) {
        return new ShrapnelApiException(409, message);
    }

    public static ShrapnelApiException conflict(String message, Object details) {
        return new ShrapnelApiException(409, message, details);
    }
}
