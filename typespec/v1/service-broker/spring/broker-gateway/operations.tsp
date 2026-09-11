/**
 * Broker Gateway Operations
 *
 * Health check and real-time traffic stream endpoints.
 */
import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace com.aibizarchitect.nexus.v1.spring.broker.gateway {

  @route("/")
  @tag("Broker Gateway")
  interface BrokerGateway {
    @doc("""
      Status: implemented.
      Health check endpoint.
      """)
    @get
    @route("/health")
    health(): HealthStatus;

    @doc("""
      Status: implemented.
      SSE stream of real-time broker traffic events.
      Clients connect via EventSource and receive BrokerTrafficEvent JSON payloads.
      """)
    @get
    @route("/api/v1/broker/logs/stream")
    streamLogs(): BrokerTrafficEvent[];

    @doc("""
      Status: implemented.
      Generic op-dispatch entry point (served by the embedded broker-service
      BrokerController). Slice-1 search seam: service `googleSearchService`
      with operation `simpleSearch` ({token, query}) or `forceSearch`
      (same params, bypasses the Mongo cache). Transport stays HTTP 200
      with `ok:false` + populated `errors[]` on failure (frozen compat for
      nexus-console's null-guard; NOT the target convention — see D3).
      rawResponse is legacy-only and out of the canonical contract (D2).
      """)
    @post
    @route("/api/v1/broker/submitRequest")
    submitRequest(@body body: ServiceRequest): ServiceResponseBody;
  }
}
