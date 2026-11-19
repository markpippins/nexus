/**
 * Broker Service Models
 * 
 * Defines the service layer models for the broker service implementation.
 */

import "../api/models.tsp";

namespace Nexus.ServiceBroker.V1 {

/**
 * Broker service configuration.
 */
model BrokerConfig {
  /**
   * The base path for broker endpoints.
   */
  basePath?: string;

  /**
   * Whether to enable request/response logging.
   */
  enableLogging?: boolean;

  /**
   * Default timeout for service invocations in milliseconds.
   */
  defaultTimeout?: int32;
}

/**
 * Result of invoking an external service operation.
 */
model InvocationResult {
  /**
   * Whether the invocation was successful.
   */
  isSuccess: boolean;

  /**
   * HTTP status code from the external service.
   */
  statusCode: int32;

  /**
   * Response body from the external service.
   */
  body: string;

  /**
   * Error message if the invocation failed.
   */
  errorMessage?: string;
}

}