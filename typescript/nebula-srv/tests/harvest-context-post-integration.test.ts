/**
 * Integration test: harvest_context tab auto-upsert on POST create with systemId.
 *
 * Test flow:
 *  1. Seed a system + harvest
 *  2. POST /api/harvest-candidates with systemId + intentDescription → auto-upsert tab
 *  3. GET /api/systems/:id/info → verify harvest_context tab exists with intent
 *  4. POST a second candidate (same system) → tab content should update (latest wins)
 *  5. POST a candidate WITHOUT systemId → tab should NOT appear
 *  6. Clean up
 *
 * Usage: npx ts-node tests/harvest-context-post-integration.test.ts
 */

import * as http from "http";

const BASE = "http://localhost:3101";

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

async function main() {
  console.log("=== harvest_context POST create integration test ===\n");

  // 1. Create a test system
  console.log("1. Creating test system...");
  const sysRes = await httpPost("/api/systems", {
    name: "HarvestContext POST Test System",
    description: "Temporary system for POST candidate → info tab testing",
  });
  assert(sysRes.status === 201, `Created system → ${sysRes.status}`);
  const systemId: string = sysRes.body.id;
  console.log(`   systemId: ${systemId}`);

  // 2. Create a test harvest
  console.log("2. Creating test harvest...");
  const harvestRes = await httpPost("/api/harvests", {
    sourcePath: "tests/harvest-context-post-integration.test.ts",
    sourceFilename: "harvest-context-post-integration.test.ts",
    model: "test",
    totalCandidates: 1,
    candidates: [],
  });
  assert(harvestRes.status === 201, `Created harvest → ${harvestRes.status}`);
  const harvestId: string = harvestRes.body.id;
  console.log(`   harvestId: ${harvestId}`);

  // 3. Verify no harvest_context tab exists yet
  console.log("3. Verifying no harvest_context tab before creating candidates...");
  const infoBefore = await httpGet(`/api/systems/${systemId}/info`);
  assert(infoBefore.status === 200, "GET system info succeeds");
  const tabsBefore: any[] = Array.isArray(infoBefore.body?.items) ? infoBefore.body.items : [];
  const hasTabBefore = tabsBefore.some((t: any) => t.tab_id === "harvest_context");
  assert(!hasTabBefore, "No harvest_context tab before creating linked candidate");

  // ════════════════════════════════════════════════════════════
  //  TEST A: Create candidate WITH systemId + intentDescription
  // ════════════════════════════════════════════════════════════

  console.log("\n--- TEST A: POST candidate with systemId + intentDescription ---\n");

  // 4. POST candidate with systemId + intentDescription → auto-upsert tab
  console.log("4. Creating linked candidate via POST...");
  const cand1Res = await httpPost("/api/harvest-candidates", {
    harvestId,
    title: "Auto-Tab Candidate One",
    intentDescription: "This is the first candidate — its intent should appear in the harvest_context tab.",
    systemId,
    tags: ["test", "post-create"],
  });
  assert(cand1Res.status === 201, `Created candidate → ${cand1Res.status}`);
  const candidate1Id: string = cand1Res.body.id;
  console.log(`   candidateId: ${candidate1Id}`);
  assert(cand1Res.body.system_id === systemId, "Candidate is linked to the system");

  // 5. Verify harvest_context tab now exists with candidate1 content
  console.log("5. Verifying harvest_context tab after first candidate POST...");
  const info1 = await httpGet(`/api/systems/${systemId}/info`);
  assert(info1.status === 200, "GET system info succeeds");
  const tabs1: any[] = Array.isArray(info1.body?.items) ? info1.body.items : [];
  const tab1 = tabs1.find((t: any) => (t.tabId ?? t.tab_id) === "harvest_context");
  assert(!!tab1, "harvest_context tab exists after POST with systemId + intent");
  assert(
    tab1.content.includes(candidate1Id),
    "Tab content includes candidate ID"
  );
  assert(
    tab1.content.includes("Auto-Tab Candidate One"),
    "Tab content includes candidate title"
  );
  assert(
    tab1.content.includes("first candidate — its intent"),
    "Tab content includes intent description"
  );
  console.log(`   Tab content preview: ${tab1.content.slice(0, 100)}...`);

  // ════════════════════════════════════════════════════════════
  //  TEST B: Create second candidate (same system) → tab updates
  // ════════════════════════════════════════════════════════════

  console.log("\n--- TEST B: POST second candidate (same system) — tab updates ---\n");

  // 6. POST a second candidate linked to the same system
  console.log("6. Creating second linked candidate via POST...");
  const cand2Res = await httpPost("/api/harvest-candidates", {
    harvestId,
    title: "Auto-Tab Candidate Two",
    intentDescription: "Second candidate — tab should now reflect this intent (latest wins).",
    systemId,
    tags: ["test", "post-create"],
  });
  assert(cand2Res.status === 201, `Created second candidate → ${cand2Res.status}`);
  const candidate2Id: string = cand2Res.body.id;
  console.log(`   candidateId: ${candidate2Id}`);

  // 7. Verify harvest_context tab now has candidate2 content (bitemporal overwrite)
  console.log("7. Verifying harvest_context tab reflects latest candidate...");
  const info2 = await httpGet(`/api/systems/${systemId}/info`);
  assert(info2.status === 200, "GET system info succeeds");
  const tabs2: any[] = Array.isArray(info2.body?.items) ? info2.body.items : [];
  const tab2 = tabs2.find((t: any) => (t.tabId ?? t.tab_id) === "harvest_context");
  assert(!!tab2, "harvest_context tab still exists");
  assert(
    tab2.content.includes("Auto-Tab Candidate Two"),
    "Tab content now shows second candidate title"
  );
  assert(
    tab2.content.includes("Second candidate — tab should now reflect"),
    "Tab content now shows second candidate intent"
  );
  console.log(`   Tab content now shows: "${tab2.content.slice(0, 80)}..."`);

  // ════════════════════════════════════════════════════════════
  //  TEST C: Create candidate WITHOUT systemId → no tab
  // ════════════════════════════════════════════════════════════

  console.log("\n--- TEST C: POST candidate WITHOUT systemId — no tab ---\n");

  // 8. Create a second system to verify isolation
  console.log("8. Creating a second system for isolation test...");
  const sys2Res = await httpPost("/api/systems", {
    name: "HarvestContext POST Isolation System",
  });
  assert(sys2Res.status === 201, `Created second system → ${sys2Res.status}`);
  const system2Id: string = sys2Res.body.id;
  console.log(`   system2Id: ${system2Id}`);

  // 9. POST candidate WITHOUT systemId
  console.log("9. Creating unlinked candidate (no systemId)...");
  const unlinkedRes = await httpPost("/api/harvest-candidates", {
    harvestId,
    title: "Unlinked Candidate",
    intentDescription: "This candidate has NO systemId — should NOT trigger tab creation.",
    tags: ["test", "unlinked"],
  });
  assert(unlinkedRes.status === 201, `Created unlinked candidate → ${unlinkedRes.status}`);
  const unlinkedId: string = unlinkedRes.body.id;
  console.log(`   candidateId: ${unlinkedId}`);
  assert(unlinkedRes.body.system_id === null, "Candidate is unlinked (system_id is null)");

  // 10. Verify BOTH systems have no unexpected tabs
  console.log("10. Verifying no tab appeared for unlinked candidate's system...");
  const infoUnlinked = await httpGet(`/api/systems/${system2Id}/info`);
  assert(infoUnlinked.status === 200, "GET second system info succeeds");
  const tabsUnlinked: any[] = Array.isArray(infoUnlinked.body?.items) ? infoUnlinked.body.items : [];
  const hasTabOnSys2 = tabsUnlinked.some((t: any) => (t.tabId ?? t.tab_id) === "harvest_context");
  assert(!hasTabOnSys2, "No harvest_context tab on system 2 (unlinked candidate)");

  // Also verify system 1 still has only its own tab (not polluted by unlinked candidate)
  const infoSys1 = await httpGet(`/api/systems/${systemId}/info`);
  const sys1Tabs: any[] = Array.isArray(infoSys1.body?.items)
    ? infoSys1.body.items.filter((t: any) => (t.tabId ?? t.tab_id) === "harvest_context")
    : [];
  assert(sys1Tabs.length === 1, "System 1 still has exactly 1 harvest_context tab");

  // ════════════════════════════════════════════════════════════
  //  TEST D: Candidate with systemId but NO intentDescription
  // ════════════════════════════════════════════════════════════

  console.log("\n--- TEST D: POST candidate with systemId but NO intentDescription ---\n");

  // 11. Create a third system + candidate without intentDescription
  console.log("11. Creating candidate with systemId but no intent...");
  const sys3Res = await httpPost("/api/systems", { name: "HarvestContext POST NoIntent System" });
  assert(sys3Res.status === 201, `Created third system → ${sys3Res.status}`);
  const system3Id: string = sys3Res.body.id;

  const noIntentRes = await httpPost("/api/harvest-candidates", {
    harvestId,
    title: "No Intent Candidate",
    systemId: system3Id,
    tags: ["test", "no-intent"],
  });
  assert(noIntentRes.status === 201, `Created candidate without intent → ${noIntentRes.status}`);
  const noIntentId: string = noIntentRes.body.id;

  // 12. Verify NO harvest_context tab created (intent_description is null)
  console.log("12. Verifying no tab when intent_description is null...");
  const infoNoIntent = await httpGet(`/api/systems/${system3Id}/info`);
  assert(infoNoIntent.status === 200, "GET third system info succeeds");
  const tabsNoIntent: any[] = Array.isArray(infoNoIntent.body?.items) ? infoNoIntent.body.items : [];
  const hasTabNoIntent = tabsNoIntent.some((t: any) => (t.tabId ?? t.tab_id) === "harvest_context");
  assert(!hasTabNoIntent, "No harvest_context tab when candidate has no intent_description");

  // ════════════════════════════════════════════════════════════
  //  CLEANUP
  // ════════════════════════════════════════════════════════════

  console.log("\n--- CLEANUP ---\n");
  console.log("13. Cleaning up test data...");

  await httpDelete(`/api/harvest-candidates/${candidate1Id}`);
  await httpDelete(`/api/harvest-candidates/${candidate2Id}`);
  await httpDelete(`/api/harvest-candidates/${unlinkedId}`);
  await httpDelete(`/api/harvest-candidates/${noIntentId}`);
  console.log("    Candidates deleted.");

  await httpDelete(`/api/harvests/${harvestId}`);
  console.log("    Harvest deleted.");

  await httpDelete(`/api/systems/${systemId}`);
  await httpDelete(`/api/systems/${system2Id}`);
  await httpDelete(`/api/systems/${system3Id}`);
  console.log("    Systems deleted.");

  console.log("\n✅ All tests passed!");
  process.exit(0);
}

main().catch((err) => {
  console.error("\n❌ Test failed:", err.message);
  process.exit(1);
});
