/**
 * Regression test: POST /api/cross-references enforces the formal
 * rel_type taxonomy (crossref-taxonomy.ts, plan #0175).
 *
 * Architect ruling 5bdce66d (T22 Step 5.4, option b): column-based linkage
 * is canonical for the causation spine; cross_references is a curated overlay
 * only. Definition of done: the create-crossref boundary must reject
 * non-taxonomy rel_types (`promotes_to`, `sourced_from`) and wrong
 * source/target pairs, while accepting a valid enum value
 * (`ag:same_thread_as` for agent_record → agent_record).
 *
 * Usage: npx tsx tests/crossref-taxonomy-validation.test.ts
 */

import * as http from "http";

const BASE = "http://localhost:3101";

// ── Helpers ───────────────────────────────────────────────────────

function httpReq(method: string, path: string, body?: any): Promise<any> {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : undefined;
    const url = new URL(path, BASE);
    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method,
      headers: bodyStr
        ? { "Content-Type": "application/json", "Content-Length": String(Buffer.byteLength(bodyStr)) }
        : {},
    };
    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk: string) => (data += chunk));
      res.on("end", () => {
        try {
          const parsed = JSON.parse(data);
          resolve({ status: res.statusCode, body: parsed });
        } catch {
          resolve({ status: res.statusCode, body: data });
        }
      });
    });
    req.on("error", reject);
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

function httpGet(path: string) { return httpReq("GET", path); }
function httpPost(path: string, body?: any) { return httpReq("POST", path, body); }
function httpDelete(path: string) { return httpReq("DELETE", path); }

function assert(condition: boolean, msg: string): void {
  if (!condition) throw new Error(`FAIL: ${msg}`);
  console.log(`  ✓ ${msg}`);
}

function assertError(res: any, expected: string, msg: string): void {
  assert(res.status === 400, `${msg} → HTTP 400 (got ${res.status})`);
  assert(
    typeof res.body?.error === "string" && res.body.error.includes(expected),
    `${msg} → error mentions "${expected}" (got ${JSON.stringify(res.body)})`
  );
}

// ── Main Test ─────────────────────────────────────────────────────

async function main() {
  console.log("=== cross-reference taxonomy enforcement test ===\n");

  // Two SELF-SEEDED agent_record ids for the valid ag:same_thread_as case.
  // The create-crossref boundary only checks source/target type + existence,
  // but a fresh database (CI) cannot rely on live rows being present — the
  // original version fetched real engineer rows and failed on an empty DB.
  const fixtureBody = (letter: string) => ({
    recordType: "report",
    role: "engineer",
    title: `crossref-taxonomy-validation fixture ${letter}`,
    content: "Self-seeded fixture for tests/crossref-taxonomy-validation.test.ts",
    tags: ["test", "crossref-taxonomy-validation"],
    level: 1,
  });
  const recA = await httpPost("/api/agent-records", fixtureBody("A"));
  assert(recA.status === 201, `Fixture A created → ${recA.status}`);
  const recB = await httpPost("/api/agent-records", fixtureBody("B"));
  assert(recB.status === 201, `Fixture B created → ${recB.status}`);
  const sourceId: string = recA.body.id;
  const targetId: string = recB.body.id;

  // 1. Non-enum rel_type: promotes_to (legacy candidate→requirement drift)
  console.log("1. Reject non-enum rel_type 'promotes_to'...");
  const promotes = await httpPost("/api/cross-references", {
    sourceType: "plan",
    sourceId: "00000000-0000-0000-0000-000000000001",
    targetType: "plan",
    targetId: "00000000-0000-0000-0000-000000000002",
    relType: "promotes_to",
  });
  assertError(promotes, "Invalid rel_type", "promotes_to rejected");

  // 2. Unprefixed legacy rel_type: sourced_from (should be kv:sourced_from)
  console.log("2. Reject unprefixed rel_type 'sourced_from'...");
  const sourced = await httpPost("/api/cross-references", {
    sourceType: "harvest",
    sourceId: "00000000-0000-0000-0000-000000000003",
    targetType: "knowledge_entity",
    targetId: "00000000-0000-0000-0000-000000000004",
    relType: "sourced_from",
  });
  assertError(sourced, "Invalid rel_type", "sourced_from rejected");

  // 3. Wrong source/target pair for a valid enum value
  console.log("3. Reject wrong source/target pair for ag:same_thread_as...");
  const wrongPair = await httpPost("/api/cross-references", {
    sourceType: "plan",
    sourceId: "00000000-0000-0000-0000-000000000001",
    targetType: "plan",
    targetId: "00000000-0000-0000-0000-000000000002",
    relType: "ag:same_thread_as",
  });
  assertError(wrongPair, "requires source_type", "ag:same_thread_as wrong-pair rejected");

  // 4. Valid enum + correct pair: ag:same_thread_as (agent_record → agent_record)
  console.log("4. Accept valid ag:same_thread_as (agent_record → agent_record)...");
  const valid = await httpPost("/api/cross-references", {
    sourceType: "agent_record",
    sourceId,
    targetType: "agent_record",
    targetId,
    relType: "ag:same_thread_as",
    metadata: { test: "crossref-taxonomy-validation" },
  });
  assert(valid.status === 201, `ag:same_thread_as accepted → HTTP 201 (got ${valid.status})`);
  assert(valid.body?.rel_type === "ag:same_thread_as", "Created row has rel_type ag:same_thread_as");
  const createdId: string = valid.body.id;

  // 5. Cleanup
  console.log("\n--- CLEANUP ---\n");
  console.log("5. Deleting the created cross-reference...");
  const del = await httpDelete(`/api/cross-references/${createdId}`);
  assert(del.status === 200 || del.status === 204, `Deleted created cross-ref → ${del.status}`);

  await httpDelete(`/api/agent-records/${sourceId}`);
  await httpDelete(`/api/agent-records/${targetId}`);
  console.log("    Fixtures deleted.");

  console.log("\n✅ All taxonomy-enforcement tests passed!");
  process.exit(0);
}

main().catch((err) => {
  console.error("\n❌ Test failed:", err.message);
  process.exit(1);
});
