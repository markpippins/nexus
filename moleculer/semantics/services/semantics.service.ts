import { Service, ServiceBroker } from "moleculer";
import { TABLES, TableMeta } from "./tables";
import {
  Req,
  healthHandler,
  metaHandler,
  canonicalAssetEnvelope,
  assetRevisionEnvelope,
  evidenceItemFilter,
  statementEvidenceFilter,
  handlersFor,
  caRevisionsList,
  caRevisionsAdd,
  caClaimsList,
  caClaimsAdd,
  caRelationsList,
  caRelationsAdd,
  claimResolve,
  caExtIdsList,
  caExtIdsAdd,
  caExtIdsRemove,
  driftResolve,
} from "./handlers";

/**
 * semantics — moleculer port of typescript/semantics-srv (canary :4160).
 *
 * PARITY CONTRACT:
 *   - store.ts + tables.ts + handlers.ts are VERBATIM ports of the
 *     incumbent's db.ts / src/tables.ts / routes/semantics.ts: the twin
 *     reads and writes the SAME PostgreSQL database (nexus DB, semantics
 *     schema fully-qualified; resolution.* for the 4 ontology tables).
 *     Parity here is against shared live state — the database is the arbiter
 *     (write-canary ruling).
 *   - The alias map in api.service.ts is the literal expansion of the live
 *     route surface, checked against typescript/semantics-srv/openapi.yaml
 *     by `make apidocs-validate` (check_drift.MOLECULER_MIRRORS).
 *
 * PARAM BAG (moleculer-web semantics): for every alias moleculer-web merges
 * path params + query string + JSON body into ONE ctx.params object (later
 * sources win). The incumbent handlers read disjoint key sets — path (:id,
 * :eid), query (limit/offset/includeExpired/evidenceType/...), body (p_* /
 * camelCase sub-resource fields) — so one bag serves all three; mkReq()
 * below just aliases the bag under the three Express-shaped views.
 * Path :id wins over a body `id` by merge order, which is exactly the
 * incumbent's `body = { ...req.body, [idParam]: req.params.id }` behavior.
 *
 * OVERRIDE SEMANTICS (mirrors the incumbent's registration order):
 *   The incumbent registers the envelope/filter routes BEFORE the per-table
 *   loop, so the special handlers win over the flat generated ones. Here the
 *   table actions themselves dispatch to the special handlers:
 *     - canonical_asset.get  → envelope (not the flat row GET)
 *     - asset_revision.get   → envelope (not the flat row GET)
 *     - evidence_item.list   → filtered list (not the flat list)
 *     - statement_evidence.list → filtered list (not the flat list)
 *   All other table actions use the generated CRUD handlers.
 *
 * STATUS CODES: write actions set ctx.meta.$statusCode = 201 (the
 * moleculer-web mechanism the kernel port established); everything else is
 * 200. Errors throw MoleculerError(message, status, code) — the gateway's
 * onError emits { error: code, message }.
 */

function mkReq(ctx: any): Req {
  const bag = (ctx.params ?? {}) as Record<string, any>;
  return { params: bag, query: bag, body: bag };
}

export default class SemanticsService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    // NOTE: Moleculer action schemas are FLAT — a nested object under an
    // action name is parsed as ONE action definition (missing its handler →
    // SERVICE_SCHEMA_ERROR at boot), not a group. Grouped actions are written
    // as flat dotted keys, exactly like the knowledge twin's
    // "entities.list" / "edges.create" convention (and the role-memory
    // twin's "x.*" names here).
    const tableActions: Record<string, any> = {};
    for (const t of TABLES) {
      const h = handlersFor(t);
      tableActions[`${t.table}.list`] = {
        // Envelope/filter override (incumbent registration-order parity):
        // list handlers return HandlerResult → unwrap .body (actions return
        // the HTTP payload itself; throws carry the status/code).
        handler:
          t.table === "evidence_item"
            ? async (ctx: any) => evidenceItemFilter(mkReq(ctx).query).then((r) => r.body)
            : t.table === "statement_evidence"
              ? async (ctx: any) => statementEvidenceFilter(mkReq(ctx).query).then((r) => r.body)
              : async (ctx: any) => h.list(mkReq(ctx).query).then((r) => r.body),
      };
      tableActions[`${t.table}.get`] = {
        handler:
          t.table === "canonical_asset"
            ? async (ctx: any) => canonicalAssetEnvelope(mkReq(ctx).params.id).then((r) => r.body)
            : t.table === "asset_revision"
              ? async (ctx: any) => assetRevisionEnvelope(mkReq(ctx).params.id).then((r) => r.body)
              : async (ctx: any) => h.get(mkReq(ctx).params.id).then((r) => r.body),
      };
      tableActions[`${t.table}.create`] = {
        handler: async (ctx: any) => {
          const r = await h.add(mkReq(ctx).body ?? {});
          (ctx.meta as any).$statusCode = r.status;
          return r.body;
        },
      };
      tableActions[`${t.table}.update`] = {
        handler: async (ctx: any) =>
          h.update(mkReq(ctx).params.id, mkReq(ctx).body ?? {}).then((r) => r.body),
      };
      tableActions[`${t.table}.remove`] = {
        handler: async (ctx: any) => h.remove(mkReq(ctx).params.id).then((r) => r.body),
      };
    }

    const xActions: Record<string, any> = {
      // ── Fixed/special routes (semantics.x.*) ───────────────────────
      // FLAT action names: Moleculer does NOT support grouping objects under
      // an action key (a nested object makes `x` itself look like an action
      // with no handler → SERVICE_SCHEMA_ERROR at boot). Dotted names are
      // still addressed as `semantics.x.<name>` — the alias map is unchanged.
      // Only the routes the table actions do NOT already serve. The alias
      // map also points a few flat forms (e.g. GET /api/canonical_asset)
      // at table actions — those entries are marked in api.service.ts.
      "x.health_get": { handler: async () => healthHandler() },
      "x.meta_get": { handler: async () => metaHandler().then((r) => r.body) },

      "x.asset_identity_claim_id_resolve_post": {
        handler: async (ctx: any) => {
          const req = mkReq(ctx);
          return claimResolve(req.params.id, req.body ?? {}).then((r) => r.body);
        },
      },
      "x.canonical_asset_id_revisions_get": {
        handler: async (ctx: any) => {
          const req = mkReq(ctx);
          return caRevisionsList(req.params.id, req.query).then((r) => r.body);
        },
      },
      "x.canonical_asset_id_revisions_post": {
        handler: async (ctx: any) => {
          const req = mkReq(ctx);
          const r = await caRevisionsAdd(req.params.id, req.body ?? {});
          (ctx.meta as any).$statusCode = r.status;
          return r.body;
        },
      },
      "x.canonical_asset_id_identity_claims_get": {
        handler: async (ctx: any) => caClaimsList(mkReq(ctx).params.id).then((r) => r.body),
      },
      "x.canonical_asset_id_identity_claims_post": {
        handler: async (ctx: any) => {
          const req = mkReq(ctx);
          const r = await caClaimsAdd(req.params.id, req.body ?? {});
          (ctx.meta as any).$statusCode = r.status;
          return r.body;
        },
      },
      "x.canonical_asset_id_relations_get": {
        handler: async (ctx: any) => caRelationsList(mkReq(ctx).params.id).then((r) => r.body),
      },
      "x.canonical_asset_id_relations_post": {
        handler: async (ctx: any) => {
          const req = mkReq(ctx);
          const r = await caRelationsAdd(req.params.id, req.body ?? {});
          (ctx.meta as any).$statusCode = r.status;
          return r.body;
        },
      },
      "x.canonical_asset_id_external_ids_get": {
        handler: async (ctx: any) => caExtIdsList(mkReq(ctx).params.id).then((r) => r.body),
      },
      "x.canonical_asset_id_external_ids_post": {
        handler: async (ctx: any) => {
          const req = mkReq(ctx);
          const r = await caExtIdsAdd(req.params.id, req.body ?? {});
          (ctx.meta as any).$statusCode = r.status;
          return r.body;
        },
      },
      "x.canonical_asset_id_external_ids_eid_delete": {
        handler: async (ctx: any) => {
          const req = mkReq(ctx);
          return caExtIdsRemove(req.params.id, req.params.eid).then((r) => r.body);
        },
      },
      "x.drift_finding_id_resolve_post": {
        handler: async (ctx: any) => {
          const req = mkReq(ctx);
          return driftResolve(req.params.id, req.body ?? {}).then((r) => r.body);
        },
      },
    };

    this.parseServiceSchema({
      name: "semantics",

      actions: {
        ...tableActions,
        ...xActions,
      },
    });
  }
}
