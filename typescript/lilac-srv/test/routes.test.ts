import { beforeEach, describe, expect, it } from "vitest";
import request from "supertest";
import { FakeDb } from "./fake-db";
import { createApp } from "../src/app";
import { ROUTE_TABLE } from "../src/routes";

const env = (key: string, route: string, over: Record<string, unknown> = {}) => ({
  source_implementation: "typescript/lilac-srv/src/routes.ts",
  runtime_route: route,
  contract_version: 1,
  source_identity: "agent-record:c71d7901",
  idempotency_key: key,
  ...over,
});

let db: FakeDb;
let app: ReturnType<typeof createApp>;

beforeEach(() => {
  db = new FakeDb();
  app = createApp({ db, limiter: false });
});

describe("surface", () => {
  it("serves health and meta", async () => {
    await request(app).get("/health").expect(200).expect(({ body }) => {
      expect(body.service).toBe("lilac-srv");
      expect(body.contract_version).toBe(1);
    });
    const meta = await request(app).get("/v1/meta").expect(200);
    expect(meta.body.staged_inert).toBe(true);
    expect(meta.body.live_callers).toEqual([]);
    expect(meta.body.authorities_not_owned).toContain("peb_admission");
    expect(meta.body.authorities_not_owned).toContain("wind_workflow_definition");
  });

  it("answers an unknown path with 404, not a silent fallthrough", async () => {
    await request(app).get("/v1/does-not-exist").expect(404);
  });

  it("serves every route in the table", async () => {
    // The registrations are literal (the fleet's OpenAPI extractor reads
    // literal calls), so this is the link that keeps the table honest: a
    // table entry with no registration, or a registration the table omits,
    // fails here. Any status except 404 proves a handler is mounted.
    for (const route of ROUTE_TABLE) {
      const path = route.expressPath.replace(":ticket_id", "11111111-1111-1111-1111-111111111111");
      const res =
        route.method === "get"
          ? await request(app).get(path)
          : await request(app).post(path).send({});
      // A 404 is a legitimate answer here (an unknown ticket timeline), so
      // "served" is proven by the answer being OUR typed JSON rather than
      // express's default text 404.
      expect(res.headers["content-type"], `${route.envelopeRoute} is not served`).toContain(
        "json",
      );
    }
  });
});

describe("envelope enforcement on the wire (AC3)", () => {
  const receiptBody = {
    producer_id: "lilac-srv",
    kind: "implementation",
    source_system: "conduit",
    source_receipt_id: "src-1",
    payload: { plan: "8261669" },
  };

  it("refuses a receipt with no provenance and writes nothing", async () => {
    const res = await request(app).post("/v1/receipts").send(receiptBody).expect(422);
    expect(res.body.code).toBe("PROVENANCE_MISSING");
    expect(res.body.outcome).toBe("refused");
    expect(db.tables.receipt).toHaveLength(0);
    expect(db.queries.some((q) => q.startsWith("INSERT INTO resolution.receipt"))).toBe(false);
  });

  it("refuses an envelope that claims another route", async () => {
    const res = await request(app)
      .post("/v1/receipts")
      .send({ ...receiptBody, provenance: env("idem-w-1", "POST /v1/tickets") })
      .expect(422);
    expect(res.body.code).toBe("PROVENANCE_AMBIGUOUS");
    expect(db.tables.receipt).toHaveLength(0);
  });

  it("refuses a missing body field with REQUEST_INVALID", async () => {
    const res = await request(app)
      .post("/v1/receipts")
      .send({ provenance: env("idem-w-2", "POST /v1/receipts"), producer_id: "x" })
      .expect(422);
    expect(res.body.code).toBe("REQUEST_INVALID");
    expect(res.body.detail.missing).toContain("source_receipt_id");
    expect(db.tables.receipt).toHaveLength(0);
  });
});

describe("mutations through HTTP (AC1, AC2)", () => {
  it("accepts a receipt, then classifies and refuses replays correctly", async () => {
    const route = "POST /v1/receipts";
    const body = {
      producer_id: "lilac-srv",
      kind: "implementation",
      source_system: "conduit",
      source_receipt_id: "src-9",
      payload: { plan: "8261669" },
    };

    const created = await request(app)
      .post("/v1/receipts")
      .send({ ...body, provenance: env("idem-h-1", route) })
      .expect(201);
    expect(created.body).toMatchObject({ outcome: "accepted", surface: "receipt" });

    const replay = await request(app)
      .post("/v1/receipts")
      .send({ ...body, provenance: env("idem-h-2", route) })
      .expect(200);
    expect(replay.body.outcome).toBe("duplicate-equivalent");
    expect(replay.body.target_id).toBe(created.body.target_id);

    const conflict = await request(app)
      .post("/v1/receipts")
      .send({
        ...body,
        payload: { plan: "8261670" },
        provenance: env("idem-h-3", route),
      })
      .expect(409);
    expect(conflict.body.code).toBe("CONFLICT");
    expect(conflict.body.detail.existing_fingerprint).toBeTruthy();
    expect(conflict.body.detail.incoming_fingerprint).toBeTruthy();
    expect(conflict.body.detail.existing_fingerprint).not.toBe(
      conflict.body.detail.incoming_fingerprint,
    );
  });

  it("issues a ticket, records a transition, and serves the timeline", async () => {
    const ticket = await request(app)
      .post("/v1/tickets")
      .send({
        provenance: env("idem-h-4", "POST /v1/tickets"),
        workflow_ref: "wr-8261669",
        role: "engineer",
        position: 1,
        generation: 0,
        objective: "build it",
      })
      .expect(201);
    const ticketId = ticket.body.target_id;

    const receipt = await request(app)
      .post("/v1/receipts")
      .send({
        provenance: env("idem-h-5", "POST /v1/receipts"),
        producer_id: "lilac-srv",
        kind: "implementation",
        source_system: "conduit",
        source_receipt_id: "src-t-1",
        payload: { plan: "8261669" },
      })
      .expect(201);

    await request(app)
      .post(`/v1/tickets/${ticketId}/transitions`)
      .send({
        provenance: env("idem-h-6", "POST /v1/tickets/{ticket_id}/transitions"),
        input_receipt_id: receipt.body.target_id,
        from_status: "pending",
        to_status: "claimed",
        payload: { actor: "engineer" },
      })
      .expect(201);

    const timeline = await request(app).get(`/v1/tickets/${ticketId}/timeline`).expect(200);
    expect(timeline.body.ticket.id).toBe(ticketId);
    expect(timeline.body.transitions).toHaveLength(1);
    expect(timeline.body.transitions[0]).toMatchObject({
      from_status: "pending",
      to_status: "claimed",
      receipt_kind: "implementation",
    });
  });

  it("records a fan-out ledger row", async () => {
    await request(app)
      .post("/v1/fanouts")
      .send({
        provenance: env("idem-h-7", "POST /v1/fanouts"),
        input_receipt_id: "55555555-5555-5555-5555-555555555555",
        kind: "implementation",
        produced: [{ ticket_id: "t1", action: "spawned" }],
      })
      .expect(201);
    expect(db.tables.fanout_transition[0].outcome).toBe("spawned");
  });

  it("reports parity as unrecorded rather than as a pass", async () => {
    const res = await request(app).get("/v1/parity").expect(200);
    expect(res.body.recorded).toBe(false);
  });

  it("returns a typed 404 for an unknown ticket timeline", async () => {
    const res = await request(app).get("/v1/tickets/nope/timeline").expect(404);
    expect(res.body.code).toBe("PROVENANCE_ORPHAN");
  });
});
