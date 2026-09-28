/**
 * HTTP contract tests.
 *
 * These boot the real Express app on an ephemeral port and exercise the routes
 * that resolve *before* touching Postgres, so the wiring is proven end to end
 * with no database and no Redis. Two of them matter beyond this service:
 *
 *   GET /segment-sets/:id            and
 *   GET /candidates/:id/segment-sets
 *
 * are the exact paths typescript/nebula-srv/src/substance-proxy.ts proxies at
 * :3115 (see routes.ts:287-330, merged in #599). If a route is renamed or its
 * ordering changes, those proxy calls start 404ing and this test fails first.
 */
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createApp, DEFAULT_PORT, SERVICE_ID, SERVICE_NAME } from "./index";
import { intQuery, requireUuidParam, HttpError, unprocessable } from "./http";

const U1 = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";

let server: Server;
let base: string;

beforeAll(async () => {
  const app = createApp();
  await new Promise<void>((resolve) => {
    server = app.listen(0, "127.0.0.1", () => resolve());
  });
  const addr = server.address() as AddressInfo;
  base = `http://127.0.0.1:${addr.port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

async function get(path: string): Promise<{ status: number; body: any }> {
  const res = await fetch(`${base}${path}`);
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { status: res.status, body };
}

describe("service identity", () => {
  it("keeps the Python service's port and registry id", () => {
    // The systemd unit and the service-registry row both key off these; a
    // silent change would orphan the running deployment.
    expect(SERVICE_NAME).toBe("substance");
    expect(SERVICE_ID).toBe(117);
    expect(DEFAULT_PORT).toBe(3115);
  });
});

describe("GET /healthz", () => {
  it("answers the liveness probe without touching Postgres", async () => {
    const res = await get("/healthz");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "ok" });
  });
});

describe("route ordering", () => {
  it("does not let /segment-sets/:id swallow the literal 'from-segments' path", async () => {
    // A POST to /segment-sets/from-segments is the strict-atomic transcript
    // ingest route. If the :id route were matched first, this would answer 422
    // with a uuid_parsing error instead of 404/422-about-the-body. Either way
    // the request must NOT be reported as a malformed UUID.
    const res = await fetch(`${base}/segment-sets/from-segments`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    });
    const body = (await res.json().catch(() => null)) as any;
    expect(res.status).toBe(422);
    // Every error must be located in the *body*, never in the path. If the
    // :segment_set_id route had matched first, the loc would be
    // ["path", "segment_set_id"] instead.
    expect(Array.isArray(body.detail)).toBe(true);
    for (const err of body.detail) {
      expect(err.loc[0]).not.toBe("path");
    }
    // And it must be the ingest route's own requirements being reported.
    const locs = body.detail.map((e: any) => e.loc.join("."));
    expect(locs).toContain("conversation_id");
    expect(locs).toContain("snapshot_id");
    expect(locs).toContain("segments");
  });

  it("rejects a non-UUID segment set id with 422, not 500", async () => {
    const res = await get("/segment-sets/not-a-uuid");
    expect(res.status).toBe(422);
    expect(res.body.detail[0].type).toBe("uuid_parsing");
    expect(res.body.detail[0].loc).toEqual(["path", "segment_set_id"]);
  });
});

describe("domain link routing", () => {
  it("404s an unsupported domain type before touching Postgres", async () => {
    // The intent-records domain was eliminated. A stale caller must get a clean
    // 404, never a 500 from a query against a dropped table.
    const res = await get(`/intent-records/${U1}/segment-sets`);
    expect(res.status).toBe(404);
    expect(res.body.detail).toBe("unknown domain_type 'intent-records'");
  });

  it("404s an unknown domain family", async () => {
    const res = await get(`/widgets/${U1}/segment-sets`);
    expect(res.status).toBe(404);
  });

  it("accepts the two surviving domain types past the domain check", async () => {
    // A 500 here means the request reached the DB — which is the point: the
    // domain guard let it through. Assert the failure is a connection error.
    const res = await get(`/candidates/${U1}/segment-sets`);
    expect(res.status).toBe(500);
    expect(res.body.detail).toBe("internal server error");
  });

  it("404s an unknown domain type on the unlink route too", async () => {
    const res = await fetch(`${base}/intent-records/${U1}/segment-sets/${U1}`, {
      method: "DELETE",
    });
    expect(res.status).toBe(404);
  });
});

describe("body validation", () => {
  it("rejects a malformed JSON body with FastAPI's 422 envelope", async () => {
    const res = await fetch(`${base}/segment-sets`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{not json",
    });
    expect(res.status).toBe(422);
    const body = (await res.json()) as any;
    expect(body.detail[0].type).toBe("json_invalid");
  });

  it("rejects a members payload that is not a list", async () => {
    const res = await fetch(`${base}/segment-sets`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ members: "not-a-list" }),
    });
    expect(res.status).toBe(422);
    const body = (await res.json()) as any;
    expect(body.detail[0].loc).toEqual(["members"]);
  });
});

describe("cors", () => {
  it("answers a cross-origin preflight, as the FastAPI app did", async () => {
    const res = await fetch(`${base}/segment-sets`, {
      method: "OPTIONS",
      headers: {
        origin: "http://localhost:4200",
        "access-control-request-method": "GET",
      },
    });
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
  });
});

describe("requireUuidParam", () => {
  it("accepts and lowercases a canonical UUID", () => {
    expect(requireUuidParam(U1.toUpperCase(), "id")).toBe(U1);
  });

  it("throws the 422 for a malformed value", () => {
    expect(() => requireUuidParam("nope", "id")).toThrow(HttpError);
    try {
      requireUuidParam("nope", "id");
    } catch (err) {
      expect((err as HttpError).status).toBe(422);
      expect((err as HttpError).detail).toEqual([
        { loc: ["path", "id"], msg: "Input should be a valid UUID", type: "uuid_parsing" },
      ]);
    }
  });

  it("throws for an absent value", () => {
    expect(() => requireUuidParam(undefined, "id")).toThrow(HttpError);
  });
});

describe("intQuery", () => {
  it("falls back to the default when absent", () => {
    expect(intQuery(undefined, 200)).toBe(200);
  });

  it("parses a numeric string", () => {
    expect(intQuery("50", 200)).toBe(50);
  });

  it("falls back when unparseable", () => {
    expect(intQuery("abc", 200)).toBe(200);
  });

  it("clamps to the bounds", () => {
    expect(intQuery("99999", 200, { max: 1000 })).toBe(1000);
    expect(intQuery("-5", 0, { min: 0 })).toBe(0);
  });
});

describe("unprocessable", () => {
  it("carries the FastAPI detail envelope", () => {
    const err = unprocessable([{ loc: ["body"], msg: "bad", type: "dict_type" }]);
    expect(err.status).toBe(422);
    expect(err.detail).toEqual([{ loc: ["body"], msg: "bad", type: "dict_type" }]);
  });
});
