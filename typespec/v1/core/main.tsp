// Shared model library — NOT a service.
// Core defines canonical DTOs that service namespaces import; it exposes no
// routes and is never emitted as a standalone service contract. (Demoted from
// @service in Wave 4 folder hygiene — service role belongs to the *-broker/
// *-registry namespaces, not to core.)
//
// N9 seed (contract-capture sweep): HealthBase + ErrorBase are the canonical
// *bases* for the health/error envelope family. Services keep their own
// response models but should EXTEND these bases (TypeSpec model spread) so
// the minimal common shape is greppable and future tooling can verify the
// family. Existing re-declarations are NOT migrated here in v1 (no breaking
// change to the nine importers) — adoption is per-service, per-PR.
namespace com.aibizarchitect.nexus.v1.core;

// ============================================================================
// N9 bases — extend, don't re-spell
// ============================================================================

/**
 * Minimal health envelope base. Every service HealthResponse should spread
 * this and add only its own probe fields:
 *   model MyHealth { ...HealthBase; connectedClients: int32; }
 * `status` is free-form string at base level because the fleet has not
 * ratified one status vocabulary (N4); tighten only after that ruling.
 */
model HealthBase {
  /** Health status — service-specific vocabulary until N4 ratifies one. */
  status: string;
  /** RFC3339 UTC instant the status was computed. */
  timestamp?: utcDateTime;
}

/**
 * Minimal error envelope base for non-2xx bodies:
 *   model MyError { ...ErrorBase; code: string; }
 */
model ErrorBase {
  /** Human-readable error message. */
  message: string;
  /** Machine-readable error code (e.g. "unknown_table", "read_only"). */
  code?: string;
  /** RFC3339 UTC instant the error was produced. */
  timestamp?: utcDateTime;
}

// ============================================================================
// Existing canonical DTOs (unchanged — nine contracts import these)
// ============================================================================

/** Binary data placeholder for canonical core. */
model BinaryData {
  /** Base64 representation of binary payload */
  base64?: string;
}

// ============================================================================
// Response Models
// ============================================================================

/** Pagination metadata — matches JVM com.aibizarchitect.nexus.v1.dto.PagedResponse.Meta */
model PagedMeta {
  /** Current page index (0-based) */
  page: int32;
  /** Items per page */
  per_page: int32;
  /** Total items across all pages */
  total: int64;
  /** Last page index */
  last_page: int32;
  /** URL for the next page (null on last page) */
  next_page_url?: string;
}

/** Generic paged wrapper for API responses — matches JVM com.aibizarchitect.nexus.v1.dto.PagedResponse */
model PagedResponse<T> {
  data: T[];
  meta: PagedMeta;
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
