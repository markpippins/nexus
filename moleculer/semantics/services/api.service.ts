import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web) — moleculer port of semantics-srv.
 *
 * The alias map is the ENTIRE parity surface: extracted by
 * tools/api-docs/extract_routes.py and checked against
 * typescript/semantics-srv/openapi.yaml by `make apidocs-validate`
 * (check_drift.MOLECULER_MIRRORS) — a renamed alias fails CI exactly like a
 * renamed Express route. The map is the LITERAL expansion of the live route
 * surface (fixed envelope/filter/sub-resource routes + the 16-table generated
 * CRUD loop); handlers themselves stay table-driven (semantics.service.ts).
 *
 * Route shape mirrors the incumbent's mounts (index.ts):
 *   app.use('/api', semanticsRouter)  → everything under /api/**
 *   app.use('/health', healthRouter)  → GET /health
 *
 * Override semantics preserved: the incumbent registers the canonical_asset /
 * asset_revision envelope GETs and the evidence_item / statement_evidence
 * filter GETs BEFORE the per-table loop, so those handlers win. Here the
 * same override is done inside the table action handlers (canonical_asset.get
 * dispatches the envelope; evidence_item.list dispatches the filter).
 *
 * NO AUTH GATE: the incumbent is CORS-only — do not add a gate the incumbent
 * does not have; parity is the contract.
 *
 * Envelope parity (semantics.service.ts throwers + onError):
 *   every failure emits the incumbent's { error: <code>, message } JSON with
 *   the per-route code (MoleculerError .type); 201s are set via
 *   ctx.meta.$statusCode; unmatched routes render the Express finalhandler
 *   HTML page byte-for-byte (kernel-port finding).
 */

function expressFullUrl(req: any): string {
  const base = String(req?.$route?.path ?? "").replace(/\/$/, "");
  return base + String(req?.url ?? req?.originalUrl ?? "");
}

function expressNotFoundHtml(req: any): string {
  return `<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error</title>\n</head>\n<body>\n<pre>Cannot ${req.method} ${expressFullUrl(req)}</pre>\n</body>\n</html>\n`;
}

const TABLE_ALIASES = [
  "relationship_type", "evidence_type", "evidence_item", "statement_evidence",
  "snapshot", "snapshot_observation", "drift_finding", "canonical_asset",
  "asset_revision", "source_observation", "asset_identity_claim", "asset_relation",
  "concept", "concept_relationship", "representation", "representation_relationship",
];

// Table CRUD pairs whose flat GET/list is overridden by a special handler
// (envelope / filter) registered in handlers.ts — documented in the alias map
// comments; the alias target is still the table action, the override happens
// inside the action handler (semantics.service.ts buildTableActions).

export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4160,
        ip: "0.0.0.0",

        routes: [
          {
            path: "/",

            // TRAP (see moleculer/README.md): `semantics.*` does NOT match
            // grouped actions — moleculer-web masks are path-style. `**` spans.
            whitelist: ["semantics.**"],

            aliases: {
              "GET /api/asset_identity_claim": "semantics.asset_identity_claim.list",
              "POST /api/asset_identity_claim": "semantics.asset_identity_claim.create",
              "GET /api/asset_identity_claim/:id": "semantics.asset_identity_claim.get",
              "PATCH /api/asset_identity_claim/:id": "semantics.asset_identity_claim.update",
              "DELETE /api/asset_identity_claim/:id": "semantics.asset_identity_claim.remove",
              "POST /api/asset_identity_claim/:id/resolve": "semantics.x.asset_identity_claim_id_resolve_post",
              "GET /api/asset_relation": "semantics.asset_relation.list",
              "POST /api/asset_relation": "semantics.asset_relation.create",
              "GET /api/asset_relation/:id": "semantics.asset_relation.get",
              "PATCH /api/asset_relation/:id": "semantics.asset_relation.update",
              "DELETE /api/asset_relation/:id": "semantics.asset_relation.remove",
              "GET /api/asset_revision": "semantics.asset_revision.list",
              "POST /api/asset_revision": "semantics.asset_revision.create",
              "GET /api/asset_revision/:id": "semantics.asset_revision.get",
              "PATCH /api/asset_revision/:id": "semantics.asset_revision.update",
              "DELETE /api/asset_revision/:id": "semantics.asset_revision.remove",
              "GET /api/canonical_asset": "semantics.canonical_asset.list",
              "POST /api/canonical_asset": "semantics.canonical_asset.create",
              "GET /api/canonical_asset/:id": "semantics.canonical_asset.get",
              "PATCH /api/canonical_asset/:id": "semantics.canonical_asset.update",
              "DELETE /api/canonical_asset/:id": "semantics.canonical_asset.remove",
              "GET /api/canonical_asset/:id/external-ids": "semantics.x.canonical_asset_id_external_ids_get",
              "POST /api/canonical_asset/:id/external-ids": "semantics.x.canonical_asset_id_external_ids_post",
              "DELETE /api/canonical_asset/:id/external-ids/:eid": "semantics.x.canonical_asset_id_external_ids_eid_delete",
              "GET /api/canonical_asset/:id/identity-claims": "semantics.x.canonical_asset_id_identity_claims_get",
              "POST /api/canonical_asset/:id/identity-claims": "semantics.x.canonical_asset_id_identity_claims_post",
              "GET /api/canonical_asset/:id/relations": "semantics.x.canonical_asset_id_relations_get",
              "POST /api/canonical_asset/:id/relations": "semantics.x.canonical_asset_id_relations_post",
              "GET /api/canonical_asset/:id/revisions": "semantics.x.canonical_asset_id_revisions_get",
              "POST /api/canonical_asset/:id/revisions": "semantics.x.canonical_asset_id_revisions_post",
              "GET /api/concept": "semantics.concept.list",
              "POST /api/concept": "semantics.concept.create",
              "GET /api/concept/:id": "semantics.concept.get",
              "PATCH /api/concept/:id": "semantics.concept.update",
              "DELETE /api/concept/:id": "semantics.concept.remove",
              "GET /api/concept_relationship": "semantics.concept_relationship.list",
              "POST /api/concept_relationship": "semantics.concept_relationship.create",
              "GET /api/concept_relationship/:id": "semantics.concept_relationship.get",
              "PATCH /api/concept_relationship/:id": "semantics.concept_relationship.update",
              "DELETE /api/concept_relationship/:id": "semantics.concept_relationship.remove",
              "GET /api/drift_finding": "semantics.drift_finding.list",
              "POST /api/drift_finding": "semantics.drift_finding.create",
              "GET /api/drift_finding/:id": "semantics.drift_finding.get",
              "PATCH /api/drift_finding/:id": "semantics.drift_finding.update",
              "DELETE /api/drift_finding/:id": "semantics.drift_finding.remove",
              "POST /api/drift_finding/:id/resolve": "semantics.x.drift_finding_id_resolve_post",
              "GET /api/evidence_item": "semantics.evidence_item.list",
              "POST /api/evidence_item": "semantics.evidence_item.create",
              "GET /api/evidence_item/:id": "semantics.evidence_item.get",
              "DELETE /api/evidence_item/:id": "semantics.evidence_item.remove",
              "GET /api/evidence_type": "semantics.evidence_type.list",
              "POST /api/evidence_type": "semantics.evidence_type.create",
              "GET /api/evidence_type/:id": "semantics.evidence_type.get",
              "PATCH /api/evidence_type/:id": "semantics.evidence_type.update",
              "DELETE /api/evidence_type/:id": "semantics.evidence_type.remove",
              "GET /api/meta": "semantics.x.meta_get",
              "GET /api/relationship_type": "semantics.relationship_type.list",
              "POST /api/relationship_type": "semantics.relationship_type.create",
              "GET /api/relationship_type/:id": "semantics.relationship_type.get",
              "PATCH /api/relationship_type/:id": "semantics.relationship_type.update",
              "DELETE /api/relationship_type/:id": "semantics.relationship_type.remove",
              "GET /api/representation": "semantics.representation.list",
              "POST /api/representation": "semantics.representation.create",
              "GET /api/representation/:id": "semantics.representation.get",
              "PATCH /api/representation/:id": "semantics.representation.update",
              "DELETE /api/representation/:id": "semantics.representation.remove",
              "GET /api/representation_relationship": "semantics.representation_relationship.list",
              "POST /api/representation_relationship": "semantics.representation_relationship.create",
              "GET /api/representation_relationship/:id": "semantics.representation_relationship.get",
              "PATCH /api/representation_relationship/:id": "semantics.representation_relationship.update",
              "DELETE /api/representation_relationship/:id": "semantics.representation_relationship.remove",
              "GET /api/snapshot": "semantics.snapshot.list",
              "POST /api/snapshot": "semantics.snapshot.create",
              "GET /api/snapshot/:id": "semantics.snapshot.get",
              "PATCH /api/snapshot/:id": "semantics.snapshot.update",
              "DELETE /api/snapshot/:id": "semantics.snapshot.remove",
              "GET /api/snapshot_observation": "semantics.snapshot_observation.list",
              "POST /api/snapshot_observation": "semantics.snapshot_observation.create",
              "GET /api/snapshot_observation/:id": "semantics.snapshot_observation.get",
              "PATCH /api/snapshot_observation/:id": "semantics.snapshot_observation.update",
              "DELETE /api/snapshot_observation/:id": "semantics.snapshot_observation.remove",
              "GET /api/source_observation": "semantics.source_observation.list",
              "POST /api/source_observation": "semantics.source_observation.create",
              "GET /api/source_observation/:id": "semantics.source_observation.get",
              "PATCH /api/source_observation/:id": "semantics.source_observation.update",
              "DELETE /api/source_observation/:id": "semantics.source_observation.remove",
              "GET /api/statement_evidence": "semantics.statement_evidence.list",
              "POST /api/statement_evidence": "semantics.statement_evidence.create",
              "GET /api/statement_evidence/:id": "semantics.statement_evidence.get",
              "PATCH /api/statement_evidence/:id": "semantics.statement_evidence.update",
              "DELETE /api/statement_evidence/:id": "semantics.statement_evidence.remove",
              "GET /health": "semantics.x.health_get",
            },

            // Unmatched aliases bypass onError entirely (moleculer-web routes
            // them through onNotFound). The incumbent is Express, whose
            // finalhandler emits the default HTML error page — reproduce it
            // byte-for-byte (kernel-port finding).
            onNotFound(req: any, res: any) {
              res.setHeader("Content-Type", "text/html; charset=utf-8");
              res.statusCode = 404;
              res.end(expressNotFoundHtml(req));
            },

            onError(req: any, res: any, err: any) {
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              // The gateway's own routing miss surfaces as moleculer-web's
              // built-in NotFoundError (name "NotFoundError"). Render the
              // Express finalhandler HTML page byte-for-byte. Action-level
              // 4xx/5xx are MoleculerError instances → JSON envelope.
              if (status === 404 && (err as any)?.name === "NotFoundError") {
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.statusCode = 404;
                res.end(expressNotFoundHtml(req));
                return;
              }
              const message = (err as any)?.message ?? String(err);
              const errCode = (err as any)?.type || (status >= 500 ? "internal_error" : "bad_request");
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              res.end(JSON.stringify({ error: errCode, message }));
            },
          },
        ],

      },
    });
  }
}

// Re-exported for tests / documentation: the table names the generated
// CRUD alias block covers (order = TABLES order in services/tables.ts).
export { TABLE_ALIASES };
