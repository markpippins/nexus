/**
 * Integration test: write-time (pr, head) idempotency for tester attestations.
 *
 * POST /api/agent-records now retires a live attestation when a new attestation
 * for the SAME (pr, head) lands (Decision 24 Amendment 1: status transition
 * only, body unchanged, metadata['record.retired'] names the successor). The pure
 * contract is unit-tested in src/__tests__/attestation-idempotency.test.ts;
 * this file exercises the wiring — the tag query and the UPDATE — against a
 * real server + PostgreSQL.
 *
 * Self-seeded (fresh-DB CI): unique PR tags, cleaned up by expiring the rows.
 *
 * Verifies:
 *  1. a second attestation for the same (pr, head) retires the first:
 *     status:retired replaces status:done, title prefixed, metadata['record.retired']
 *     names the successor, and the shape/binding tags survive
 *  2. the successor stays live
 *  3. a HEAD-LESS legacy row is never retired (nothing to key it on; the
 *     679/681 shared pair must not be collapsed)
 *  4. an attestation for a DIFFERENT head is never retired
 *
 * Usage: npx tsx tests/attestation-write-idempotency.test.ts
 */

import * as http from "http";

const BASE = process.env.NEBULA_TEST_BASE || "http://localhost:3101";

const PR_SAME_HEAD = "491920";
const PR_HEADLESS = "491921";
const PR_OTHER_HEAD = "491922";

const HEAD_A = "abcdef1234567890abcdef1234567890abcdef12";
const HEAD_B = "1234567890abcdef1234567890abcdef12345678";

function httpReq(
  method: string,
  path: string,
  body?: any,
): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : undefined;
    const url = new URL(path, BASE);
    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + (body ? "" : url.search),
      method,
      headers: bodyStr
        ? {
            "Content-Type": "application/json",
            "Content-Length": String(Buffer.byteLength(bodyStr)),
          }
        : {},
    };
    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk: string) => (data += chunk));
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode!, body: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode!, body: data });
        }
      });
    });
    req.on("error", (err) => reject(new Error(`Request failed: ${err.message}`)));
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

function assert(label: string, condition: boolean, detail?: string): void {
  if (!condition) {
    console.error(`  ✗ FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
    process.exit(1);
  }
  console.log(`  ✓ ${label}`);
}

async function post(tags: string[], recordType = "assessment") {
  return httpReq("POST", "/api/agent-records", {
    recordType,
    role: "tester",
    title: "attestation-write-idempotency fixture",
    content:
      "Self-seeded fixture for tests/attestation-write-idempotency.test.ts. " +
      "Body must survive retirement unchanged.",
    tags,
    level: 1,
  });
}

async function get(id: string) {
  return httpReq("GET", `/api/agent-records/${id}`);
}

async function run() {
  console.log(`attestation write-time idempotency test against ${BASE}`);
  const created: string[] = [];
  const track = (r: { status: number; body: any }, what: string) => {
    assert(`${what} created`, r.status === 201, `got ${r.status}`);
    created.push(r.body.id);
    return r.body;
  };

  // ── 1. same (pr, head): the first row is retired under the second ──
  const first = track(
    await post([
      "type:approval",
      "status:done",
      `pr:${PR_SAME_HEAD}`,
      `head:${HEAD_A}`,
      "policy:attestation/v2",
      "verdict:approved",
    ]),
    "first attestation",
  );
  const second = track(
    await post([
      "type:approval",
      "status:done",
      `pr:${PR_SAME_HEAD}`,
      `head:${HEAD_A}`,
      "policy:attestation/v2",
      "verdict:approved",
    ]),
    "second attestation (same pr+head)",
  );

  const back = await get(first.id);
  assert("retired row readable (200)", back.status === 200, `got ${back.status}`);
  const tags: string[] = back.body.tags || [];
  assert("predecessor carries status:retired", tags.includes("status:retired"));
  assert("predecessor no longer carries status:done", !tags.includes("status:done"));
  assert(
    "binding tags survive retirement",
    tags.includes(`pr:${PR_SAME_HEAD}`) && tags.includes(`head:${HEAD_A}`) &&
      tags.includes("type:approval"),
  );
  assert(
    "title is prefixed [RETIRED …]",
    String(back.body.title || "").startsWith("[RETIRED "),
    back.body.title,
  );
  assert(
    "body is unchanged (status transition only)",
    String(back.body.content || "").includes("Body must survive retirement unchanged."),
  );
  assert(
    "metadata['record.retired'] names the successor",
    String(back.body.metadata?.["record.retired"]?.note || "").includes(second.id.slice(0, 8)),
    JSON.stringify(back.body.metadata?.["record.retired"] || null),
  );

  const live = await get(second.id);
  assert(
    "successor stays live",
    (live.body.tags || []).includes("status:done") &&
      !(live.body.tags || []).includes("status:retired"),
  );

  // ── 2. head-less legacy row is never collapsed ──
  const legacy = track(
    await post([`pr:${PR_HEADLESS}`, "type:attestation", "status:done"], "report"),
    "head-less legacy attestation",
  );
  track(
    await post([
      "type:approval",
      "status:done",
      `pr:${PR_HEADLESS}`,
      `head:${HEAD_A}`,
    ]),
    "head-bound attestation for the same PR",
  );
  const legacyBack = await get(legacy.id);
  assert(
    "head-less row is NOT retired (no binding to collapse it on)",
    (legacyBack.body.tags || []).includes("status:done") &&
      !(legacyBack.body.tags || []).includes("status:retired"),
    JSON.stringify(legacyBack.body.tags),
  );

  // ── 3. a different head is a different attestation ──
  const otherHead = track(
    await post([
      "type:approval",
      "status:done",
      `pr:${PR_OTHER_HEAD}`,
      `head:${HEAD_A}`,
    ]),
    "attestation at head A",
  );
  track(
    await post([
      "type:approval",
      "status:done",
      `pr:${PR_OTHER_HEAD}`,
      `head:${HEAD_B}`,
    ]),
    "attestation at head B (same PR)",
  );
  const otherBack = await get(otherHead.id);
  assert(
    "row for another head is untouched",
    (otherBack.body.tags || []).includes("status:done") &&
      !(otherBack.body.tags || []).includes("status:retired"),
  );

  // ── cleanup: expire every fixture row ──
  for (const id of created) {
    const r = await httpReq("DELETE", `/api/agent-records/${id}`);
    assert(`cleanup ${id.slice(0, 8)}`, r.status === 200, `got ${r.status}`);
  }

  console.log("write-time idempotency: all assertions passed");
}

run().catch((err) => {
  console.error(`  ✗ FAIL: ${err?.message || err}`);
  process.exit(1);
});
