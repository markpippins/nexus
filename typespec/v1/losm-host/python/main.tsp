/**
 * losm-host — consolidated LOSM serving surface (decision 5d8e10fd).
 * Serves the native LOSM API plus the absorbed vision-srv /api surface.
 */
import "./models.tsp";
import "./operations.tsp";

@service(#{ title: "Nexus LOSM Host API (vision-srv consolidated)" })
namespace org.nexus.losmhost;
