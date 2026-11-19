namespace com.aibizarchitect.nexus.v1.servicebroker.core;

/** Binary data placeholder for canonical core. */
model BinaryData {
  /** Base64 representation of binary payload */
  base64?: string;
}

// ============================================================================
// Response Models
// ============================================================================

/** Generic paged wrapper for API responses. */
model PagedResponse<T> {
  data: T[];
  totalElements: int64;
  totalPages: int32;
  number: int32; // current page index (0-based)
  numberOfElements: int32;
  size: int32;
}

/**
 * Error response structure.
 */
model ErrorResponse {
  /** Error message */
  message: string;

  /** HTTP status code */
  status: int32;

  /** Timestamp when error occurred */
  timestamp: utcDateTime;

  /** Request path that caused the error */
  path?: string;

  /** Detailed error details */
  details?: string[];
}