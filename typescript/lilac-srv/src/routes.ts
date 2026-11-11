/**
 * The Lilac surface. One table drives three things: the express registration,
 * the envelope's expected route, and the OpenAPI contract test — so a route
 * cannot exist in the server without being documented, or be documented
 * without a handler (AC1).
 *
 * Every mutation is a POST that must carry `provenance` in its body. There is
 * deliberately NO admission route and NO workflow-definition route: PEB owns
 * admission and Wind owns workflow definitions, and this surface must not
 * become a second authority for either (AC7).
 */
import { Router, type Request, type Response, type NextFunction } from "express";
import {
  parseEnvelope,
  ContractRefusal,
  type Envelope,
  type Surface,
} from "./contract";
import type { LilacRepository } from "./repository";

export type HttpMethod = "get" | "post";

export interface RouteSpec {
  operationId: string;
  method: HttpMethod;
  /** Path as documented in openapi.yaml (OpenAPI templating). */
  openapiPath: string;
  /** Path as registered with express (colon templating). */
  expressPath: string;
  /** `${METHOD} ${openapiPath}` — what the envelope's runtime_route must say. */
  envelopeRoute: string;
  mutation: Surface | null;
  /** Body fields that must be present on a mutation, checked before SQL. */
  requiredFields?: string[];
}

export const ROUTE_TABLE: RouteSpec[] = [
  {
    operationId: "health",
    method: "get",
    openapiPath: "/health",
    expressPath: "/health",
    envelopeRoute: "GET /health",
    mutation: null,
  },
  {
    operationId: "meta",
    method: "get",
    openapiPath: "/v1/meta",
    expressPath: "/v1/meta",
    envelopeRoute: "GET /v1/meta",
    mutation: null,
  },
  {
    operationId: "issueReceipt",
    method: "post",
    openapiPath: "/v1/receipts",
    expressPath: "/v1/receipts",
    envelopeRoute: "POST /v1/receipts",
    mutation: "receipt",
    requiredFields: ["producer_id", "kind", "source_system", "source_receipt_id", "payload"],
  },
  {
    operationId: "issueTicket",
    method: "post",
    openapiPath: "/v1/tickets",
    expressPath: "/v1/tickets",
    envelopeRoute: "POST /v1/tickets",
    mutation: "ticket",
    requiredFields: ["workflow_ref", "role", "position"],
  },
  {
    operationId: "recordTransition",
    method: "post",
    openapiPath: "/v1/tickets/{ticket_id}/transitions",
    expressPath: "/v1/tickets/:ticket_id/transitions",
    envelopeRoute: "POST /v1/tickets/{ticket_id}/transitions",
    mutation: "ticket_transition",
    requiredFields: ["input_receipt_id", "from_status", "to_status"],
  },
  {
    operationId: "recordFanout",
    method: "post",
    openapiPath: "/v1/fanouts",
    expressPath: "/v1/fanouts",
    envelopeRoute: "POST /v1/fanouts",
    mutation: "fanout_transition",
    requiredFields: ["input_receipt_id", "kind", "produced"],
  },
  {
    operationId: "ticketTimeline",
    method: "get",
    openapiPath: "/v1/tickets/{ticket_id}/timeline",
    expressPath: "/v1/tickets/:ticket_id/timeline",
    envelopeRoute: "GET /v1/tickets/{ticket_id}/timeline",
    mutation: null,
  },
  {
    operationId: "parityDiagnostics",
    method: "get",
    openapiPath: "/v1/parity",
    expressPath: "/v1/parity",
    envelopeRoute: "GET /v1/parity",
    mutation: null,
  },
];

function requireFields(body: Record<string, unknown>, fields: string[]): void {
  const missing = fields.filter((f) => body[f] === undefined || body[f] === null);
  if (missing.length > 0) {
    throw new ContractRefusal(
      "REQUEST_INVALID",
      422,
      `request refused: missing ${missing.join(", ")}`,
      { missing },
    );
  }
}

function asBody(req: Request): Record<string, unknown> {
  const body = req.body;
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new ContractRefusal("REQUEST_INVALID", 422, "request refused: body must be an object", {});
  }
  return body as Record<string, unknown>;
}

export function buildRouter(repo: LilacRepository, contractVersion: number): Router {
  const router = Router();
  const handlers: Record<string, (req: Request, res: Response) => Promise<void>> = {
    health: async (_req, res) => {
      res.json({ status: "ok", service: "lilac-srv", contract_version: contractVersion });
    },

    meta: async (_req, res) => {
      res.json({
        service: "lilac-srv",
        contract_version: contractVersion,
        // Staged-inert by construction: no live caller is repointed at this
        // surface, and the service itself never writes outside resolution.*.
        staged_inert: true,
        live_callers: [],
        mutations: ["receipt", "ticket", "ticket_transition", "fanout_transition"],
        authorities_not_owned: ["peb_admission", "wind_workflow_definition"],
      });
    },

    issueReceipt: async (req, res) => {
      const body = asBody(req);
      requireFields(body, ROUTE_TABLE[2].requiredFields ?? []);
      const envelope: Envelope = parseEnvelope(body.provenance, "POST /v1/receipts");
      const result = await repo.issueReceipt({
        envelope,
        producer_id: String(body.producer_id),
        kind: String(body.kind),
        source_system: String(body.source_system),
        source_receipt_id: String(body.source_receipt_id),
        payload: (body.payload ?? {}) as Record<string, unknown>,
        refs: (body.refs ?? {}) as Record<string, unknown>,
      });
      res.status(result.outcome === "accepted" ? 201 : 200).json(result);
    },

    issueTicket: async (req, res) => {
      const body = asBody(req);
      requireFields(body, ["workflow_ref", "role", "position"]);
      const envelope: Envelope = parseEnvelope(body.provenance, "POST /v1/tickets");
      const result = await repo.issueTicket({
        envelope,
        workflow_ref: String(body.workflow_ref),
        role: String(body.role),
        position: Number(body.position),
        generation: body.generation === undefined ? 0 : Number(body.generation),
        objective: body.objective === undefined ? "" : String(body.objective),
        predecessor_receipt_id:
          body.predecessor_receipt_id === undefined || body.predecessor_receipt_id === null
            ? null
            : String(body.predecessor_receipt_id),
      });
      res.status(result.outcome === "accepted" ? 201 : 200).json(result);
    },

    recordTransition: async (req, res) => {
      const body = asBody(req);
      requireFields(body, ["input_receipt_id", "from_status", "to_status"]);
      const envelope: Envelope = parseEnvelope(
        body.provenance,
        "POST /v1/tickets/{ticket_id}/transitions",
      );
      const result = await repo.recordTransition({
        envelope,
        ticket_id: String(req.params.ticket_id),
        input_receipt_id: String(body.input_receipt_id),
        from_status: String(body.from_status),
        to_status: String(body.to_status),
        fanout_policy_version:
          body.fanout_policy_version === undefined ? 1 : Number(body.fanout_policy_version),
        outcome_class: body.outcome_class === undefined ? undefined : String(body.outcome_class),
        payload: (body.payload ?? {}) as Record<string, unknown>,
      });
      res.status(result.outcome === "accepted" ? 201 : 200).json(result);
    },

    recordFanout: async (req, res) => {
      const body = asBody(req);
      requireFields(body, ["input_receipt_id", "kind", "produced"]);
      const envelope: Envelope = parseEnvelope(body.provenance, "POST /v1/fanouts");
      const result = await repo.recordFanout({
        envelope,
        input_receipt_id: String(body.input_receipt_id),
        kind: String(body.kind),
        fan_out_policy_version:
          body.fan_out_policy_version === undefined ? 1 : Number(body.fan_out_policy_version),
        produced: (body.produced ?? []) as Record<string, unknown>[],
        outcome: body.outcome === undefined ? undefined : String(body.outcome),
      });
      res.status(result.outcome === "accepted" ? 201 : 200).json(result);
    },

    ticketTimeline: async (req, res) => {
      res.json(await repo.ticketTimeline(String(req.params.ticket_id)));
    },

    parityDiagnostics: async (req, res) => {
      const limit = req.query.limit === undefined ? 5 : Number(req.query.limit);
      res.json(await repo.parityDiagnostics(Number.isFinite(limit) ? limit : 5));
    },
  };

  for (const route of ROUTE_TABLE) {
    const handler = handlers[route.operationId];
    if (!handler) {
      throw new Error(`route table entry ${route.operationId} has no handler`);
    }
    router[route.method](route.expressPath, (req, res, next) => {
      handler(req, res).catch(next);
    });
  }

  return router;
}

/** Express error boundary: typed refusals keep their status and code, anything
 *  else becomes a 500 that does not leak driver internals. */
export function errorHandler(
  err: unknown,
  _req: Request,
  res: Response,
  _next: NextFunction,
): void {
  if (err instanceof ContractRefusal) {
    res.status(err.status).json(err.toBody());
    return;
  }
  const message = err instanceof Error ? err.message : String(err);
  console.error("[lilac-srv] unhandled route error:", message);
  res.status(500).json({ outcome: "refused", code: "INTERNAL", message });
}
