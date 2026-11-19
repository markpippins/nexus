namespace Nexus.ServiceBroker.V1.Api {

/**
 * Broker Service API Models
 * 
 * Defines the core data contracts for submitting requests and receiving responses
 * from the service broker.
 */

/**
 * Error detail structure for failed responses.
 */
model ResponseError {
  /**
   * The field associated with the error.
   */
  field: string;

  /**
   * The error message.
   */
  message: string;
}

/**
 * A request submitted to the service broker for processing.
 */
model ServiceRequest {
  /**
   * The target service name to invoke.
   */
  service: string;

  /**
   * The operation name to execute on the target service.
   */
  operation: string;

  /**
   * Parameters to pass to the operation.
   */
  params: Record<unknown>;

  /**
   * Unique identifier for tracking this request.
   */
  requestId: string;

  /**
   * Whether the request/response should be encrypted.
   */
  encrypt?: boolean;
}

/**
 * The response body returned by broker service operations.
 * This is the concrete type used in API responses.
 */
model ServiceResponseBody {
  /**
   * Whether the operation was successful.
   */
  ok: boolean;

  /**
   * The response data (present when ok is true).
   */
  data?: unknown;

  /**
   * Error details (present when ok is false).
   */
  errors?: ResponseError[];

  /**
   * The request ID this response corresponds to.
   */
  requestId: string;

  /**
   * Timestamp when the response was generated.
   */
  ts: utcDateTime;

  /**
   * API version.
   */
  version?: string;

  /**
   * The service that processed the request.
   */
  service?: string;

  /**
   * The operation that was executed.
   */
  operation?: string;

  /**
   * Whether the response is encrypted.
   */
  encrypt?: boolean;
}

}