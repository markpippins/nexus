/**
 * Governed requirement compilation capability API.
 * This describes the versioned API contract; it grants no execution authority.
 */
import "@typespec/http";
import "./models.tsp";
import "./operations.tsp";

@service(#{ title: "Nexus Requirement Compilation Capability" })
namespace org.nexus.requirementcompile;
