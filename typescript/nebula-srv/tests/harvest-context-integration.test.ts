/**
 * Integration test: harvest_context info tab auto-upsert on candidate linking.
 *
 * Test flow:
 *  1. Seed a system + harvest (or use existing)
 *  2. POST /api/harvest-candidates → create candidate without systemId
 *  3. PATCH /api/harvest-candidates/:id with systemId → triggers auto-upsert
 *  4. GET /api/systems/:id/info → verify harvest_context tab exists with intent
 *  5. DELETE /api/systems/:id/info/harvest_context → unlink
 *  6. GET /api/harvest-candidates/:id → verify system_id is null
 *
 * Usage: npx ts-node tests/harvest-context-integration.test.ts
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
      path: url.pathname,
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
function httpPatch(path: string, body?: any) { return httpReq("PATCH", path, body); }
function httpDelete(path: string) { return httpReq("DELETE", path); }

function assert(condition: boolean, msg: string): void {
  if (!condition) throw new Error(`FAIL: ${msg}`);
  console.log(`  ✓ ${msg}`);
}

// ── Main Test ─────────────────────────────────────────────────────

async function main() {
  console.log("=== harvest_context integration test ===\n");

  // 1. Create a test system
  console.log("1. Creating test system...");
  const sysRes = await httpPost("/api/systems", {
    name: "Harvest Context Test System",
    description: "Temporary system for integration testing",
  });
  assert(sysRes.status === 201, `Created system → ${sysRes.status}`);
  const systemId: string = sysRes.body.id;
  console.log(`   systemId: ${systemId}`);

  // 2. Create a test harvest
  console.log("2. Creating test harvest...");
  const harvestRes = await httpPost("/api/harvests", {
    sourcePath: "tests/harvest-context-integration.test.ts",
    sourceFilename: "harvest-context-integration.test.ts",
    model: "test",
    totalCandidates: 1,
    candidates: [
      {
        title: "Test Candidate",
        intentDescription: "This is a test intent for harvest_context verification.",
        implementationNotes: [],
        codeSnippets: [],
        openQuestions: [],
        tags: ["test"],
      },
    ],
  });
  assert(harvestRes.status === 201, `Created harvest → ${harvestRes.status}`);
  const harvestId: string = harvestRes.body.id;
  console.log(`   harvestId: ${harvestId}`);

  // 3. Create a candidate WITHOUT systemId (unlinked)
  console.log("3. Creating unlinked candidate...");
  const candidateRes = await httpPost("/api/harvest-candidates", {
    harvestId,
    title: "Test Candidate for Linking",
    intentDescription: "Intent: verify that linking a candidate auto-creates a harvest_context tab.",
    tags: ["test"],
  });
  assert(candidateRes.status === 201, `Created candidate → ${candidateRes.status}`);
  const candidateId: string = candidateRes.body.id;
  console.log(`   candidateId: ${candidateId}`);
  assert(candidateRes.body.system_id === null, "Candidate initially unlinked (system_id is null)");

  // 4. Verify no harvest_context tab exists yet
  console.log("4. Verifying no harvest_context tab before link...");
  const infoBefore = await httpGet(`/api/systems/${systemId}/info`);
  assert(infoBefore.status === 200, "GET system info succeeds");
  const tabsBefore: any[] = Array.isArray(infoBefore.body?.items) ? infoBefore.body.items : [];
  const hasTabBefore = tabsBefore.some((t: any) => (t.tabId ?? t.tab_id) === "harvest_context");
  assert(!hasTabBefore, "No harvest_context tab before linking");

  // 5. PATCH candidate with systemId → triggers auto-upsert
  console.log("5. Linking candidate to system via PATCH...");
  const patchRes = await httpPatch(`/api/harvest-candidates/${candidateId}`, {
    systemId,
  });
  assert(patchRes.status === 200, `PATCH succeeded → ${patchRes.status}`);
  assert(patchRes.body.system_id === systemId, "candidate.system_id is now set");

  // 6. Verify harvest_context tab now exists
  console.log("6. Verifying harvest_context tab after link...");
  const infoAfter = await httpGet(`/api/systems/${systemId}/info`);
  assert(infoAfter.status === 200, "GET system info succeeds");
  const tabs: any[] = Array.isArray(infoAfter.body?.items) ? infoAfter.body.items : [];
  const harvestTab = tabs.find((t: any) => (t.tabId ?? t.tab_id) === "harvest_context");
  assert(!!harvestTab, "harvest_context tab exists after linking");
  assert(
    harvestTab.content.includes(candidateId),
    "Tab content includes the candidate ID"
  );
  assert(
    harvestTab.content.includes("Test Candidate for Linking"),
    "Tab content includes the candidate title"
  );
  assert(
    harvestTab.content.includes("Intent: verify that linking"),
    "Tab content includes the intent description"
  );
  console.log(`   Tab content preview: ${harvestTab.content.slice(0, 100)}...`);

  // 7. DELETE the harvest_context tab → triggers unlink
  console.log("7. Deleting harvest_context tab (should unlink candidate)...");
  const deleteRes = await httpDelete(`/api/systems/${systemId}/info/harvest_context`);
  assert(deleteRes.status === 200, `DELETE tab succeeded → ${deleteRes.status}`);

  // 8. Verify candidate is unlinked
  console.log("8. Verifying candidate unlinked after tab delete...");
  const candidateAfterDelete = await httpGet(`/api/harvest-candidates/${candidateId}`);
  assert(candidateAfterDelete.status === 200, "GET candidate succeeds");
  assert(
    candidateAfterDelete.body.system_id === null,
    "candidate.system_id is null after tab delete"
  );

  // 9. Verify tab is gone
  console.log("9. Verifying tab no longer appears...");
  const infoAfterDelete = await httpGet(`/api/systems/${systemId}/info`);
  const tabsAfterDelete: any[] = Array.isArray(infoAfterDelete.body?.items) ? infoAfterDelete.body.items : [];
  const hasTabAfterDelete = tabsAfterDelete.some((t: any) => (t.tabId ?? t.tab_id) === "harvest_context");
  assert(!hasTabAfterDelete, "harvest_context tab is gone after delete");

  // 10. Clean up
  console.log("10. Cleaning up test data...");
  await httpDelete(`/api/harvest-candidates/${candidateId}`);
  await httpDelete(`/api/harvests/${harvestId}`);
  await httpDelete(`/api/systems/${systemId}`);
  console.log("    Test data cleaned up.");

  console.log("\n✅ All tests passed!");
  process.exit(0);
}

main().catch((err) => {
  console.error("\n❌ Test failed:", err.message);
  process.exit(1);
});
