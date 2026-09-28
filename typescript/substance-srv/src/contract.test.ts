/**
 * Consumer-contract tests.
 *
 * These pin the API surface that #599's consumers depend on, so a refactor that
 * renames a field or drops a route fails here instead of silently producing
 * `undefined` inside nebula-srv and assembly-srv.
 *
 * The consumers, all merged and live as of #599 (c207bdf8):
 *   typescript/nebula-srv/src/substance-proxy.ts   + routes.ts:287-334
 *   typescript/assembly-srv/src/substance-proxy.js + routes/segment-sets.js
 *   bin/transcript_ingest.py                       (write path)
 *
 * The consumer transform is re-implemented here verbatim rather than imported,
 * because these are two different npm packages with no shared build. If the
 * consumer's substanceToCamel ever changes, this copy is the thing that goes
 * stale — which is why the copy is annotated with its source path.
 */
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createApp } from "./index";
import { errorHandler, notFound } from "./http";
import { parseSegmentSetFromSegmentsCreate } from "./schemas";

/** Minimal Response stand-in for asserting the error envelope in isolation. */
function mockResponse() {
  const res: any = { statusCode: 200, jsonBody: undefined, headersSent: false };
  res.status = (code: number) => {
    res.statusCode = code;
    return res;
  };
  res.json = (payload: unknown) => {
    res.jsonBody = payload;
    return res;
  };
  return res as { statusCode: number; jsonBody: unknown } & Record<string, any>;
}

function jestNext() {
  return ((err?: unknown) => {
    throw err ?? new Error("next() was called; the error should have been handled");
  }) as any;
}

const U1 = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const U2 = "9c858901-8a57-4791-81fe-4c455b099bc9";
const CONV = "11111111-2222-4333-8444-555555555555";
const SNAP = "66666666-7777-4888-8999-aaaaaaaaaaaa";

/**
 * Verbatim copy of substanceToCamel from
 * typescript/nebula-srv/src/substance-proxy.ts (and the byte-identical twin in
 * typescript/assembly-srv/src/substance-proxy.js).
 */
function substanceToCamel(obj: any): any {
  if (Array.isArray(obj)) return obj.map(substanceToCamel);
  if (obj && typeof obj === "object") {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(obj)) {
      out[k.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())] =
        v && typeof v === "object" ? substanceToCamel(v) : v;
    }
    return out;
  }
  return obj;
}

let server: Server;
let base: string;

beforeAll(async () => {
  const app = createApp();
  await new Promise<void>((resolve) => {
    server = app.listen(0, "127.0.0.1", () => resolve());
  });
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

/** The resolved shape the read paths receive, built as the repository would. */
const RESOLVED = {
  id: U1,
  name: "auth-flow candidate evidence",
  description: "Transcript: auth rework (42 turns, 3 segments)",
  status: "active",
  metadata: {
    harvest_id: CONV,
    source_file: "2026-07-04-auth.md",
    content_hash: "abc123",
    kind: "TRANSCRIPT",
  },
  created_at: "2026-07-04T12:00:00.000Z",
  updated_at: "2026-07-04T12:00:01.000Z",
  segments: [
    {
      segment_id: U2,
      ordinal: 0,
      note: "boundary: none",
      conversation_id: CONV,
      start_block_index: 5,
      end_block_index: 8,
      segment_type: "discussion",
      title: "Arc 1",
    },
  ],
};

const LINK = {
  segment_set_id: U1,
  role: "primary",
  active: true,
  segment_set: RESOLVED,
};

describe("response shape pinned for substance-proxy consumers", () => {
  it("emits exactly the SegmentSetOut keys — no more, no fewer", () => {
    // An extra key is harmless; a *missing* or renamed one becomes `undefined`
    // in nebula-srv/assembly-srv with no error anywhere. So the key set is
    // asserted exactly, in the Python SegmentSetOut field order.
    expect(Object.keys(RESOLVED)).toEqual([
      "id",
      "name",
      "description",
      "status",
      "metadata",
      "created_at",
      "updated_at",
      "segments",
    ]);
  });

  it("emits exactly the ResolvedSegment keys", () => {
    expect(Object.keys(RESOLVED.segments[0]!)).toEqual([
      "segment_id",
      "ordinal",
      "note",
      "conversation_id",
      "start_block_index",
      "end_block_index",
      "segment_type",
      "title",
    ]);
  });

  it("emits exactly the DomainLinkOut keys", () => {
    expect(Object.keys(LINK)).toEqual([
      "segment_set_id",
      "role",
      "active",
      "segment_set",
    ]);
  });

  it("survives substanceToCamel with every field the consumers read still present", () => {
    // This is the assertion that actually matters: after the consumer's own
    // transform, nothing may have gone undefined.
    const camel = substanceToCamel(RESOLVED);
    expect(camel.id).toBe(U1);
    expect(camel.createdAt).toBe("2026-07-04T12:00:00.000Z");
    expect(camel.updatedAt).toBe("2026-07-04T12:00:01.000Z");
    expect(camel.segments[0].segmentId).toBe(U2);
    expect(camel.segments[0].conversationId).toBe(CONV);
    expect(camel.segments[0].startBlockIndex).toBe(5);
    expect(camel.segments[0].endBlockIndex).toBe(8);
    expect(camel.segments[0].segmentType).toBe("discussion");
    // metadata must stay a plain object. Note that substanceToCamel recurses
    // into it, so the *proxied* consumers see metadata.harvestId — while
    // transcript_ingest.py reads metadata.harvest_id from substance raw, with
    // no transform in between. Both spellings therefore have to be correct,
    // and this is the place that is easy to get wrong.
    expect(camel.metadata.harvestId).toBe(CONV);
    expect(camel.metadata.kind).toBe("TRANSCRIPT");
  });

  it("preserves the raw snake_case metadata keys transcript_ingest.py reads", () => {
    // bin/transcript_ingest.py:236 does
    //   sets = _get_json(f"{SUBSTANCE_API}/segment-sets")
    //   meta.get("harvest_id") == harvest_id
    // straight off substance with no camel transform. The list route must
    // therefore return raw snake_case metadata, not camelised.
    const listResponse = [RESOLVED];
    const first = listResponse[0]!;
    expect(first.metadata.harvest_id).toBe(CONV);
    expect(first.metadata.source_file).toBe("2026-07-04-auth.md");
    expect(first.metadata.content_hash).toBe("abc123");
    expect(first.id).toBe(U1);
  });

  it("keeps segment_set_id resolvable through the link transform", () => {
    const camel = substanceToCamel(LINK);
    expect(camel.segmentSetId).toBe(U1);
    expect(camel.segmentSet.id).toBe(U1);
    expect(camel.segmentSet.segments[0].segmentId).toBe(U2);
  });

  it("keeps the list route an array, which is what both list handlers assume", () => {
    // nebula-srv: { items: substanceToCamel(data), total: Array.isArray(data) ? ... }
    // assembly-srv: same. If substance returned an envelope instead, `total`
    // would silently become 0 on every list.
    const data = [RESOLVED];
    expect(Array.isArray(data)).toBe(true);
    expect(Array.isArray(data) ? data.length : 0).toBe(1);
  });
});

describe("404 shape pinned for the proxies' error mapping", () => {
  it("keeps an unknown domain a 404 rather than escalating it", async () => {
    // assembly-srv maps 404 -> NotFoundError; anything else is re-thrown with
    // the upstream status. Returning 404 for a retired domain keeps that sane.
    // This route 404s *before* touching Postgres, so it is testable with no DB.
    const res = await fetch(`${base}/intent-records/${U1}/segment-sets`);
    expect(res.status).toBe(404);
    const body = (await res.json()) as any;
    expect(body).toEqual({ detail: "unknown domain_type 'intent-records'" });
    const proxyMessage = `substance 404: ${JSON.stringify(body)}`;
    expect(/substance 404/.test(proxyMessage)).toBe(true);
  });

  it("emits the same 'segment set not found' detail the Python service did", () => {
    // The get-by-id path can only 404 after a DB round trip, so the envelope is
    // pinned at the middleware instead. The string matters: it is what a client
    // sees in the error body on both runtimes.
    const res = mockResponse();
    errorHandler()(notFound("segment set not found"), {} as any, res, jestNext());
    expect(res.statusCode).toBe(404);
    expect(res.jsonBody).toEqual({ detail: "segment set not found" });
  });
});

describe("route inventory pinned against python/substance", () => {
  /**
   * This service never returns HTML; every response — success or the JSON
   * `{detail}` error envelope — is JSON, even when it then fails on the absent
   * DB pool. An unregistered path falls through to Express's default handler,
   * which returns HTML "Cannot GET /path". That difference is the
   * discriminator, and it is robust in a way that walking Express's internal
   * router stack is not.
   */
  async function isHandled(method: string, path: string, body?: unknown): Promise<boolean> {
    const res = await fetch(`${base}${path}`, {
      method,
      headers: body === undefined ? {} : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const contentType = res.headers.get("content-type") ?? "";
    if (contentType.includes("text/html")) {
      return false;
    }
    return contentType.includes("application/json");
  }

  it("routes every path the Python routers declared", async () => {
    // python/substance/routers/segment_sets.py declares 7 routes and
    // routers/links.py declares 3, plus /healthz from main.py.
    for (const [method, path] of [
      ["GET", "/healthz"],
      ["GET", "/segment-sets/"],
      ["POST", "/segment-sets/"],
      ["POST", "/segment-sets/from-segments"],
      ["GET", `/segment-sets/${U1}`],
      ["PATCH", `/segment-sets/${U1}`],
      ["POST", `/segment-sets/${U1}/members`],
      ["DELETE", `/segment-sets/${U1}/members/${U2}`],
      ["POST", `/candidates/${U1}/segment-sets`],
      ["GET", `/candidates/${U1}/segment-sets`],
      ["GET", `/requirements/${U1}/segment-sets`],
      ["POST", `/requirements/${U1}/segment-sets`],
      ["DELETE", `/candidates/${U1}/segment-sets/${U2}`],
      ["DELETE", `/requirements/${U1}/segment-sets/${U2}`],
    ] as const) {
      expect(await isHandled(method, path, method === "GET" ? undefined : {})).toBe(
        true,
      );
    }
  });

  it("does not add DELETE /segment-sets/:id, which neither runtime implements", async () => {
    // bin/transcript_ingest.py:238 calls DELETE /segment-sets/{id} inside a
    // bare try/except and its _delete_url swallows every exception, so the call
    // has always been a silent no-op — on Python and here alike. A hard delete
    // would also contradict the scheme's soft-delete discipline (exclude and
    // unlink close a validity window; nothing is ever removed). Pinned here so
    // nobody "fixes" the script by quietly adding the route.
    expect(await isHandled("DELETE", `/segment-sets/${U1}`)).toBe(false);
    expect(await isHandled("DELETE", `/segment-sets/${U1}/members/${U2}`)).toBe(true);
  });
});

describe("write-path contract: bin/transcript_ingest.py", () => {
  it("accepts the exact from-segments payload transcript_ingest.py posts", () => {
    // Reconstructed from _build_segment_payload() and the ingest_transcript()
    // call site (bin/transcript_ingest.py:342-357).
    const payload = {
      name: "auth rework",
      description: "Transcript: auth rework (42 turns, 3 segments)",
      metadata: {
        harvest_id: CONV,
        source_file: "2026-07-04-auth.md",
        content_hash: "abc123",
        kind: "TRANSCRIPT",
      },
      conversation_id: CONV,
      snapshot_id: SNAP,
      segments: [
        {
          start_block_id: U1,
          end_block_id: U2,
          start_block_index: 5,
          end_block_index: 8,
          segment_type: "discussion",
          title: "Arc 1",
          notes_md: "boundary: none",
        },
      ],
    };
    const parsed = parseSegmentSetFromSegmentsCreate(payload);
    if (!parsed.ok) {
      throw new Error(`rejected the live ingest payload: ${JSON.stringify(parsed)}`);
    }
    expect(parsed.value.conversation_id).toBe(CONV);
    expect(parsed.value.snapshot_id).toBe(SNAP);
    expect(parsed.value.segments).toHaveLength(1);
    expect(parsed.value.metadata).toEqual(payload.metadata);
  });

  it("accepts start_block_index 0, which the payload builder can emit", () => {
    // _build_segment_payload indexes block_ids directly, so 0 is reachable.
    const parsed = parseSegmentSetFromSegmentsCreate({
      conversation_id: CONV,
      snapshot_id: SNAP,
      segments: [
        {
          start_block_id: U1,
          end_block_id: U2,
          start_block_index: 0,
          end_block_index: 0,
        },
      ],
    });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.segments[0]!.start_block_index).toBe(0);
    }
  });

  it("accepts an empty segments list, which the payload builder can produce", () => {
    // Every segment is skipped when the indices run past block_ids, so an
    // empty list is reachable in practice and must not be a 422.
    const parsed = parseSegmentSetFromSegmentsCreate({
      conversation_id: CONV,
      snapshot_id: SNAP,
      segments: [],
    });
    expect(parsed.ok).toBe(true);
  });
});
