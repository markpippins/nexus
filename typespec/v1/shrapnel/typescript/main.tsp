/**
 * shrapnel — Nexus Shrapnel EAV Object Store API
 *
 * REST service over the `shrapnel` PostgreSQL schema: an entity-attribute-value
 * (EAV) object store with typed extension tables (value_long, value_string,
 * value_double, value_boolean, value_timestamp, value_jsonb, value_uuid),
 * field metadata, object classification into stereotype revisions, and
 * on-demand conformance evaluation.
 *
 * Dual-runtime alignment contract: BOTH implementations conform to this
 * surface —
 *   - typescript/shrapnel  (Express; routes mounted at /api)
 *   - jvm/spring/nexus-core/nexus-core-shrapnel (Spring; mounted at /api/shrapnel
 *     inside the nexus-core monolith)
 *
 * The TS service is the reference shape; the JVM port mirrors it
 * statement-for-statement. Drift between either implementation and this
 * contract is caught by tools/api-docs drift coverage.
 *
 * Auth posture: LAN-bound, no authentication. Documented AS SHIPPED per
 * audit 8bfe6519 convention.
 */
import "./models.tsp";
import "./operations.tsp";

@service(#{ title: "Shrapnel EAV Object Store API — shrapnel" })
namespace org.nexus.shrapnel;
