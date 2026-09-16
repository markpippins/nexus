/**
 * Nexus TypeSpec Contracts — Root Entry Point
 *
 * Umbrella entry point for generating the full Nexus OpenAPI specification
 * and multi-language client SDKs. Imports all real service contracts:
 *
 *   - Core (shared types)
 *   - Service Registry (lookup entities, CRUD operations)
 *   - Service Broker (request routing, broker gateway)
 *     - API layer (public contract)
 *     - SPI layer (service provider interface)
 *     - Service layer (implementation endpoints)
 *   - File Service (file system operations)
 *   - Terrain Topology (servers, MCP servers, runnable services, CLI tools)
 *   - PEB Kernel (governance engine, decision ledger, state hashing)
 *
 * Each sub-package is independently compilable via its own tspconfig.yaml.
 * This root config aggregates them for holistic code generation.
 */

// Core shared types — PagedResponse, BinaryData, ErrorResponse
import "./core/main.tsp";

// User API — shared user DTOs
import "./user-api/main.tsp";

// Service Registry — framework vendors, categories, languages, services, deployments
import "./service-registry/spring/main.tsp";

// Service Broker — request submission, broker gateway, file service
import "./service-broker/spring/main.tsp";

// Terrain Topology — servers, MCP servers, runnable services, CLI tools, profiles
import "./terrain/spring/main.tsp";

// PEB Kernel — governance engine, decision ledger, capability enforcement
import "./peb-kernel/spring/main.tsp";

// Governance Admission Envelope — canonical wire boundary (W1.05)
import "./governance-envelope/spring/main.tsp";

// SOLScript — in-memory resolution-schema interpreter; model-only contract
// (route-free; nats-envelope precedent). The Moleculer REST facade declares
// its own operations over these models.
import "./solscript/python/main.tsp";

// Write-Queue — mobile/disconnected reconciliation-intent protocol surface.
// Reuses the nats-envelope CanonicalEnvelope; model-only. The Java mobile
// write-queue produces entries; the Python cascade reconciler consumes them
// and emits canonical events on nexus.events.
import "./write-queue/main.tsp";
