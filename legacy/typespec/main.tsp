import "@typespec/http";
import "@typespec/rest";

// Service Core API (canonical core)
import "./core/models.tsp";
import "./service-broker/api/models.tsp";
import "./service-broker/api/operations.tsp";
import "./service-broker/spi/models.tsp";
import "./service-broker/spi/operations.tsp";
import "./service-broker/service/models.tsp";
import "./service-broker/service/operations.tsp";
import "./service-registry/models.tsp";
using Http; 
using Rest;
using com.aibizarchitect.nexus.v1.servicebroker.api;
using com.aibizarchitect.nexus.v1.servicebroker.spi;
using com.aibizarchitect.nexus.v1.servicebroker.service;
using com.aibizarchitect.nexus.v1.serviceregistry;
using com.aibizarchitect.nexus.v1.servicebroker.core;

@service(#{ title: "Nexus" })
namespace com.aibizarchitect.nexus.v1;


@route("/broker")
interface Broker {
  @post
  submit(@body body: ServiceRequest): ServiceResponseBody;
}

@route("/registry")
interface ServiceRegistry {
  @get
  getFrameworkVendor(@path id: int64): FrameworkVendor;
}