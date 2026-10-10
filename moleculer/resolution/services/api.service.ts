import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the resolution-srv twin.
 *
 * Dispatch-through-Express (fleet twin pattern): the incumbent serves a
 * registry-driven surface — GET /api/{table} + GET /api/{table}/{id} for
 * every table in src/tables.ts, plus /api/meta, /api/health and /health,
 * with a uniform 405 read_only guard on every non-GET method.
 *
 * DECISION 32 (4c9afc09, Option 3): the aliases below are LITERAL — one
 * per concrete table — GENERATED from the twin's verbatim tables.ts copy
 * (48 tables; fixed: /health + /api/meta), NOT a single :table param route. Literal aliases keep the
 * twin's surface enumerable by the existing extractor, avoid param
 * shadowing of /api/meta and /api/health, and make per-table presence
 * first-class. The registry-closure test (fleet guard) pins
 * tables.ts <-> aliases byte-identity and count equality on both sides.
 *
 * The fixed-route half of the contract of record is the COMPILED TypeSpec
 * contract (typespec/v1/resolution-srv/generated/schema/openapi.yaml);
 * check_drift.py holds the twin-vs-(compiled-fixed ∪ tables-enumerated)
 * equality from the other side.
 *
 * CATCH-ALL (dispatch parity): the incumbent routes EVERYTHING under /api
 * into its Express router — unmatched methods hit the 405 boundary and
 * unknown paths hit Express's own 404s. A literal-alias-only gateway would
 * answer those from moleculer-web instead (JSON NotFoundError envelope —
 * NOT byte-identical). The method-wildcard aliases below, registered
 * LAST so every literal keeps precedence, forward all remaining /api
 * traffic into the same verbatim Express app, making the twin's unmatched
 * surface byte-identical to the incumbent's (405 read_only, 404
 * unknown_table, Express 404s). NOTE on syntax: moleculer-web 0.10.x pins
 * path-to-regexp ^3.1.0, where a bare `*` in a PATH is a LITERAL — the
 * catch-all form is `(.*)`; bare `/api` needs its own alias. Non-/api paths
 * are not aliased: the incumbent serves Express's default HTML 404 there,
 * the twin moleculer-web's JSON 404 — a gateway-plumbing residual outside
 * the /api contract surface, recorded here rather than papered over.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],
      settings: {
        port: process.env.SERVICE_PORT || 4171,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["resolution.**"],
            bodyParsers: { json: true },
            // moleculer-web 0.10.x does NOT put $req/$res into action meta on
            // its own (only passReqResToParams aliases get them, via params).
            // Dispatch-through-Express needs the REAL req/res objects inside
            // the action handler, so inject them here — this hook runs on the
            // gateway context BEFORE broker.call, and gateway context meta
            // propagates to the action context.
            onBeforeCall: (ctx: any, _route: any, req: any, res: any) => {
              ctx.meta.$req = req;
              ctx.meta.$res = res;
            },
            aliases: {
              "GET /health": "resolution.dispatch",
              "GET /api/meta": "resolution.dispatch",
              "GET /api/assertion_evaluation": "resolution.dispatch",
              "GET /api/assertion_evaluation/:id": "resolution.dispatch",
              "GET /api/concept": "resolution.dispatch",
              "GET /api/concept/:id": "resolution.dispatch",
              "GET /api/concept_attribute": "resolution.dispatch",
              "GET /api/concept_attribute/:id": "resolution.dispatch",
              "GET /api/concept_attribute_binding": "resolution.dispatch",
              "GET /api/concept_attribute_binding/:id": "resolution.dispatch",
              "GET /api/concept_attribute_value": "resolution.dispatch",
              "GET /api/concept_attribute_value/:id": "resolution.dispatch",
              "GET /api/concept_relationship": "resolution.dispatch",
              "GET /api/concept_relationship/:id": "resolution.dispatch",
              "GET /api/concept_relationship_binding": "resolution.dispatch",
              "GET /api/concept_relationship_binding/:id": "resolution.dispatch",
              "GET /api/concept_state_transition": "resolution.dispatch",
              "GET /api/concept_state_transition/:id": "resolution.dispatch",
              "GET /api/consumer_operation": "resolution.dispatch",
              "GET /api/consumer_operation/:id": "resolution.dispatch",
              "GET /api/contract_version": "resolution.dispatch",
              "GET /api/contract_version/:id": "resolution.dispatch",
              "GET /api/enforcement_posture": "resolution.dispatch",
              "GET /api/enforcement_posture/:id": "resolution.dispatch",
              "GET /api/entity": "resolution.dispatch",
              "GET /api/entity/:id": "resolution.dispatch",
              "GET /api/execution_admission_receipt": "resolution.dispatch",
              "GET /api/execution_admission_receipt/:id": "resolution.dispatch",
              "GET /api/execution_claim": "resolution.dispatch",
              "GET /api/execution_claim/:id": "resolution.dispatch",
              "GET /api/execution_claim_evidence": "resolution.dispatch",
              "GET /api/execution_claim_evidence/:id": "resolution.dispatch",
              "GET /api/execution_evidence": "resolution.dispatch",
              "GET /api/execution_evidence/:id": "resolution.dispatch",
              "GET /api/expression": "resolution.dispatch",
              "GET /api/expression/:id": "resolution.dispatch",
              "GET /api/expression_operand": "resolution.dispatch",
              "GET /api/expression_operand/:id": "resolution.dispatch",
              "GET /api/frame_dimension": "resolution.dispatch",
              "GET /api/frame_dimension/:id": "resolution.dispatch",
              "GET /api/frame_dimension_meaning": "resolution.dispatch",
              "GET /api/frame_dimension_meaning/:id": "resolution.dispatch",
              "GET /api/frame_dimension_value": "resolution.dispatch",
              "GET /api/frame_dimension_value/:id": "resolution.dispatch",
              "GET /api/function_binding": "resolution.dispatch",
              "GET /api/function_binding/:id": "resolution.dispatch",
              "GET /api/governance_threshold": "resolution.dispatch",
              "GET /api/governance_threshold/:id": "resolution.dispatch",
              "GET /api/identity_strategy": "resolution.dispatch",
              "GET /api/identity_strategy/:id": "resolution.dispatch",
              "GET /api/implementation_plan": "resolution.dispatch",
              "GET /api/implementation_plan/:id": "resolution.dispatch",
              "GET /api/observation": "resolution.dispatch",
              "GET /api/observation/:id": "resolution.dispatch",
              "GET /api/producer_refusals": "resolution.dispatch",
              "GET /api/producer_refusals/:id": "resolution.dispatch",
              "GET /api/producer_registry": "resolution.dispatch",
              "GET /api/producer_registry/:id": "resolution.dispatch",
              "GET /api/proposition": "resolution.dispatch",
              "GET /api/proposition/:id": "resolution.dispatch",
              "GET /api/proposition_assertion": "resolution.dispatch",
              "GET /api/proposition_assertion/:id": "resolution.dispatch",
              "GET /api/proposition_comparison": "resolution.dispatch",
              "GET /api/proposition_comparison/:id": "resolution.dispatch",
              "GET /api/proposition_frame_value": "resolution.dispatch",
              "GET /api/proposition_frame_value/:id": "resolution.dispatch",
              "GET /api/receipt": "resolution.dispatch",
              "GET /api/receipt/:id": "resolution.dispatch",
              "GET /api/representation": "resolution.dispatch",
              "GET /api/representation/:id": "resolution.dispatch",
              "GET /api/representation_comparison": "resolution.dispatch",
              "GET /api/representation_comparison/:id": "resolution.dispatch",
              "GET /api/representation_identity": "resolution.dispatch",
              "GET /api/representation_identity/:id": "resolution.dispatch",
              "GET /api/representation_relationship": "resolution.dispatch",
              "GET /api/representation_relationship/:id": "resolution.dispatch",
              "GET /api/requirement": "resolution.dispatch",
              "GET /api/requirement/:id": "resolution.dispatch",
              "GET /api/rule": "resolution.dispatch",
              "GET /api/rule/:id": "resolution.dispatch",
              "GET /api/semantic_type": "resolution.dispatch",
              "GET /api/semantic_type/:id": "resolution.dispatch",
              "GET /api/semantic_type_required_dimension": "resolution.dispatch",
              "GET /api/semantic_type_required_dimension/:id": "resolution.dispatch",
              "GET /api/specification": "resolution.dispatch",
              "GET /api/specification/:id": "resolution.dispatch",
              "GET /api/specification_lineage": "resolution.dispatch",
              "GET /api/specification_lineage/:id": "resolution.dispatch",
              "GET /api/ticket": "resolution.dispatch",
              "GET /api/ticket/:id": "resolution.dispatch",
              "GET /api/ticket_transition": "resolution.dispatch",
              "GET /api/ticket_transition/:id": "resolution.dispatch",
              "GET /api/verified_statement": "resolution.dispatch",
              "GET /api/verified_statement/:id": "resolution.dispatch",
              "GET /api/work_request": "resolution.dispatch",
              "GET /api/work_request/:id": "resolution.dispatch",
              "GET /api/work_request_edge": "resolution.dispatch",
              "GET /api/work_request_edge/:id": "resolution.dispatch",
              // Registered LAST: catch-alls into the verbatim Express app for
              // everything the literals don't name (405 boundary, unknown
              // tables/paths). `(.*)` = path-to-regexp 3.x catch-all; bare
              // `/api` is a separate alias. See the CATCH-ALL note above.
              "* /api": "resolution.dispatch",
              "* /api/(.*)": "resolution.dispatch",
            },
          },
        ],
      },
    });
  }
}
