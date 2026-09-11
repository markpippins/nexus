/**
 * Broker Gateway Models
 *
 * Health check and traffic event models for the broker gateway.
 */
import "../../../core/main.tsp";

namespace com.aibizarchitect.nexus.v1.spring.broker.gateway {

  /** Health check response */
  model HealthStatus {
    status: string;
    service: string;
    timestamp: utcDateTime;
    /** Arbitrary JSON with additional health details */
    details?: unknown;
  }

  /** Broker traffic event published to SSE stream */
  model BrokerTrafficEvent {
    eventId: string;
    timestamp: string;
    durationMs: int64;
    requestId: string;
    service: string;
    operation: string;
    ok: boolean;
    httpStatus: int32;
    source: string;
    errorMessage?: string;
  }

  /** Generic op-dispatch request (BrokerController.submitRequest). */
  model ServiceRequest {
    /** Target service, e.g. `googleSearchService`. */
    service: string;
    /** Target operation, e.g. `simpleSearch` / `forceSearch`. */
    operation: string;
    /** Op params (opaque per service/operation). */
    params?: unknown;
    /** Caller correlation id, echoed in the response. */
    requestId: string;
  }

  /** Typed service error (slice-1 D3, shared shape with moleculer SearchError). */
  model ServiceError {
    /** Machine-readable code. */
    code: string;
    /** Human-readable message. */
    message: string;
    /** Whether retrying can succeed. */
    retryable: boolean;
  }

  /** Generic op-dispatch response. Transport stays HTTP 200 (frozen compat);
      failure is signaled by `ok:false` + populated `errors` (D3). */
  model ServiceResponseBody {
    ok: boolean;
    data?: unknown;
    errors?: ServiceError[];
    requestId: string;
    ts: string;
    version: string;
    service: string;
    operation: string;
    encrypt: boolean;
  }
}
