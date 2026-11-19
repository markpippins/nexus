/**
 * Broker Service Operations
 * 
 * Defines the concrete service operations for the broker implementation.
 */

import "@typespec/http";
import "../api/models.tsp";
import "./models.tsp";

using Http;
using Nexus.ServiceBroker.V1.Api;

/**
 * Broker service implementation.
 *
 * This service handles request submission and response processing.
 */
@route("/api/v1/broker")
namespace Nexus.ServiceBroker.V1 {
  /**
   * Submit a request to the broker for processing.
   */
  @route("/submitRequest")
  @post
  op submitRequest(request: ServiceRequest): ServiceResponseBody;

  /**
   * Test endpoint for broker functionality.
   */
  @route("/testBroker")
  @post
  op testBroker(): ServiceResponseBody;

  /**
   * Invoke an operation on an external service.
   */
  @route("/invokeExternalService")
  @post
  @doc("Invoke an external service operation")
  op invokeExternalService(
    /**
     * The operation name to invoke.
     */
    @query operation: string,

    /**
     * The request body to send.
     */
    @body requestBody: unknown
  ): InvocationResult;

  /**
   * Perform a health check on an external service.
   */
  @route("/healthCheck")
  @post
  @doc("Health check for an external service")
  op healthCheck(
    /**
     * The name of the service to check.
     */
    serviceName: string
  ): boolean;
}
