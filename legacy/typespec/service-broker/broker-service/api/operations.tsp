import "@typespec/http";
import "./models.tsp";

namespace Nexus.ServiceBroker.V1.Api {

/**
 * Broker Service API Operations
 * 
 * Defines the REST API operations for submitting requests to the service broker.
 */

using Http;

/**
 * Submit a request to the service broker for processing.
 * 
 * This endpoint accepts a service request and routes it to the appropriate
 * target service for execution.
 */
@route("/api/v1/submitRequest")
@post
op submitRequest(
  /**
   * The service request to process.
   */
  @body request: ServiceRequest
): ServiceResponseBody;

}
