import "@typespec/http";
import "../api/models.tsp";

using Http;
using Nexus.ServiceBroker.V1.Api;

namespace Nexus.ServiceBroker.V1.Spi {

/**
 * Broker Service SPI Operations
 * 
 * Defines the internal operations for service broker implementations.
 * These operations are used internally by the broker infrastructure.
 */

/**
 * Internal operation to process a submitted request.
 * 
 * This is the core operation that broker implementations must provide.
 */
@route("/processRequest")
@post
op processRequest(request: ServiceRequest): ServiceResponseBody;

}