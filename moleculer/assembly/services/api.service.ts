import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway for the assembly-srv twin.
 *
 * Dispatch-through-Express (fleet twin pattern): the incumbent serves 88
 * registered endpoints across 73 paths (forums/threads/comments, feed,
 * work-requests, plans, agendas, counts, search, the read-only
 * segment-set evidence surface). AppError envelopes ({error, code?,
 * constraint?}) and the custom zlib gzip middleware pass through the
 * verbatim stack unchanged.
 *
 * NO AUTH GATE: the incumbent is CORS + express.json only — parity is
 * the contract.
 *
 * The alias map is GENERATED from typescript/assembly-srv/openapi.yaml
 * (the committed contract) — one alias per declared (method, path), so
 * gateway surface == contract surface by construction; check_drift.py
 * holds the equality from the other side. Paths carry the /api prefix
 * matching the incumbent's app.use("/api", routes) mount; the unprefixed
 * static /health is declared in the contract and aliased here.
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],
      settings: {
        port: process.env.SERVICE_PORT || 4107,
        ip: "0.0.0.0",
        routes: [
          {
            path: "/",
            whitelist: ["assembly.**"],
            // Incumbent raises the json body limit (typescript/assembly-srv/
            // src/index.js: express.json({ limit: '5mb' }) for transcript
            // ingest comments) — the gateway parser must match or large
            // writes 413 here before the verbatim stack ever sees them.
            bodyParsers: { json: { limit: "5mb" } },
            // moleculer-web 0.10.x does NOT put $req/$res into action meta on
            // its own (only passReqResToParams aliases get them, via params).
            // Dispatch-through-Express needs the REAL req/res objects inside
            // the action handler, so inject them here — this hook runs on the
            // gateway context BEFORE broker.call, and gateway context meta
            // propagates to the action context. (Discovered LIVE on the
            // resolution twin: without this, every aliased call 500s
            // DISPATCH_NO_REQRES; jest suites hit the Express app directly
            // and never see it.)
            onBeforeCall: (ctx: any, _route: any, req: any, res: any) => {
              ctx.meta.$req = req;
              ctx.meta.$res = res;
            },
            aliases: {
              "GET /api/agendas": "assembly.dispatch",
              "GET /api/agendas/:id": "assembly.dispatch",
              "GET /api/agendas/:id/items": "assembly.dispatch",
              "GET /api/agent-records": "assembly.dispatch",
              "GET /api/agent-records/:id": "assembly.dispatch",
              "GET /api/assessments": "assembly.dispatch",
              "GET /api/assessments/:id": "assembly.dispatch",
              "GET /api/bridges/agendas-by-forum/:forumId": "assembly.dispatch",
              "GET /api/bridges/artifact-refs/:postId": "assembly.dispatch",
              "GET /api/bridges/artifact-threads/:type/:id": "assembly.dispatch",
              "DELETE /api/bridges/forum-agenda": "assembly.dispatch",
              "POST /api/bridges/forum-agenda": "assembly.dispatch",
              "GET /api/bridges/forums-by-agenda/:agendaId": "assembly.dispatch",
              "DELETE /api/bridges/post-artifact": "assembly.dispatch",
              "POST /api/bridges/post-artifact": "assembly.dispatch",
              "POST /api/bridges/supporting-refs": "assembly.dispatch",
              "GET /api/bridges/supporting-refs/comment/:commentId": "assembly.dispatch",
              "GET /api/bridges/supporting-refs/post/:postId": "assembly.dispatch",
              "GET /api/candidates": "assembly.dispatch",
              "GET /api/candidates/:id": "assembly.dispatch",
              "GET /api/candidates/:id/segment-sets": "assembly.dispatch",
              "GET /api/conversations": "assembly.dispatch",
              "GET /api/conversations/:id": "assembly.dispatch",
              "GET /api/counts": "assembly.dispatch",
              "GET /api/decisions": "assembly.dispatch",
              "POST /api/decisions": "assembly.dispatch",
              "GET /api/duality/sessions/:threadId/events": "assembly.dispatch",
              "POST /api/duality/sessions/:threadId/messages": "assembly.dispatch",
              "GET /api/duality/turns": "assembly.dispatch",
              "GET /api/duality/turns/:turnId": "assembly.dispatch",
              "GET /api/duality/turns/latest": "assembly.dispatch",
              "POST /api/duality/watches": "assembly.dispatch",
              "GET /api/duality/watches/:threadId": "assembly.dispatch",
              "GET /api/duality/watches/active": "assembly.dispatch",
              "GET /api/feed": "assembly.dispatch",
              "POST /api/feed": "assembly.dispatch",
              "DELETE /api/feed/:id": "assembly.dispatch",
              "GET /api/forums": "assembly.dispatch",
              "POST /api/forums": "assembly.dispatch",
              "DELETE /api/forums/:id": "assembly.dispatch",
              "PUT /api/forums/:id": "assembly.dispatch",
              "GET /api/forums/:slug/threads": "assembly.dispatch",
              "POST /api/forums/:slug/threads": "assembly.dispatch",
              "GET /api/forums/by-id/:forumId/threads": "assembly.dispatch",
              "POST /api/forums/by-id/:forumId/threads": "assembly.dispatch",
              "GET /api/forums/by-id/:id": "assembly.dispatch",
              "GET /api/forums/by-slug/:slug": "assembly.dispatch",
              "DELETE /api/forums/comments/:id": "assembly.dispatch",
              "GET /api/forums/comments/:id": "assembly.dispatch",
              "PUT /api/forums/comments/:id": "assembly.dispatch",
              "POST /api/forums/move-thread": "assembly.dispatch",
              "PUT /api/forums/reorder": "assembly.dispatch",
              "GET /api/forums/search/by-name": "assembly.dispatch",
              "GET /api/forums/search/by-thread-title": "assembly.dispatch",
              "GET /api/forums/threads/:threadId": "assembly.dispatch",
              "PUT /api/forums/threads/:threadId": "assembly.dispatch",
              "DELETE /api/forums/threads/:threadId/comments": "assembly.dispatch",
              "POST /api/forums/threads/:threadId/comments": "assembly.dispatch",
              "PUT /api/forums/threads/:threadId/status": "assembly.dispatch",
              "GET /api/harvests": "assembly.dispatch",
              "GET /api/harvests/:id": "assembly.dispatch",
              "GET /api/health": "assembly.dispatch",
              "GET /api/observations": "assembly.dispatch",
              "GET /api/observations/:id": "assembly.dispatch",
              "GET /api/open-questions": "assembly.dispatch",
              "POST /api/open-questions": "assembly.dispatch",
              "GET /api/open-questions/:id": "assembly.dispatch",
              "GET /api/open-questions/:id/answers": "assembly.dispatch",
              "POST /api/open-questions/:id/answers": "assembly.dispatch",
              "GET /api/open-questions/:id/timeline": "assembly.dispatch",
              "GET /api/plans": "assembly.dispatch",
              "GET /api/plans/:id": "assembly.dispatch",
              "POST /api/refresh-stats": "assembly.dispatch",
              "GET /api/requirements": "assembly.dispatch",
              "GET /api/requirements/:id": "assembly.dispatch",
              "GET /api/requirements/:id/segment-sets": "assembly.dispatch",
              "GET /api/search": "assembly.dispatch",
              "GET /api/segment-sets": "assembly.dispatch",
              "GET /api/segment-sets/:id": "assembly.dispatch",
              "GET /api/specifications": "assembly.dispatch",
              "GET /api/specifications/:id": "assembly.dispatch",
              "GET /api/users": "assembly.dispatch",
              "POST /api/users": "assembly.dispatch",
              "GET /api/users/:id": "assembly.dispatch",
              "GET /api/users/by-alias/:alias": "assembly.dispatch",
              "GET /api/work-requests": "assembly.dispatch",
              "GET /api/work-requests/:id": "assembly.dispatch",
              "GET /health": "assembly.dispatch",
              // Registered LAST: catch-alls into the verbatim Express app for
              // everything the literals don't name (unmatched paths anywhere,
              // GET-only routes hit with other methods). `(.*)` =
              // path-to-regexp 3.x catch-all; bare `/` is a separate alias.
              "* /": "assembly.dispatch",
              "* /(.*)": "assembly.dispatch",
            },
          },
        ],
      },
    });
  }
}
