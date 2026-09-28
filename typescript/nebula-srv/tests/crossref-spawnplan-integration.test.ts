/**
 * Integration test: cross_reference creation on candidate → plan linking.
 *
 * Test flow:
 *  1. Seed a system + harvest + unlinked candidate
 *  2. PATCH /api/harvest-candidates/:id with systemId + planRef
 *  3. GET /api/cross-references?sourceType=harvest_candidate&sourceId=<candidateId>
 *     → verify the cross-reference exists with rel_type='ag:spawns_plan'
 *  4. Verify the reverse lookup: GET /api/plans/:planRef/candidates returns the candidate
 *  5. Also test the spawn-requirement endpoint: unlink first, then POST
 *     spawn-requirement (verb renamed from spawn-plan per decision 319defa5)
 *  6. Clean up
 *
 * Usage: npx ts-node tests/crossref-spawnplan-integration.test.ts
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
function httpPatch(path: string, body?: any) { return httpReq("PATCH", path, body); }
function httpDelete(path: string) { return httpReq("DELETE", path); }

function assert(condition: boolean, msg: string): void {
  if (!condition) throw new Error(`FAIL: ${msg}`);
  console.log(`  ✓ ${msg}`);
}

// ── Main Test ─────────────────────────────────────────────────────

async function main() {
  console.log("=== cross_reference spawn-plan integration test ===\n");

  // 1. Create a test system
  console.log("1. Creating test system...");
  const sysRes = await httpPost("/api/systems", {
    name: "CrossRef Test System",
    description: "Temporary system for cross-ref integration testing",
  });
  assert(sysRes.status === 201, `Created system → ${sysRes.status}`);
  const systemId: string = sysRes.body.id;
  console.log(`   systemId: ${systemId}`);

  // 2. Create a test subsystem (needed for spawn-plan requirement)
  console.log("2. Creating test subsystem...");
  const subRes = await httpPost("/api/subsystems", {
    systemId,
    name: "CrossRef Test Subsystem",
  });
  assert(subRes.status === 201, `Created subsystem → ${subRes.status}`);
  const subsystemId: string = subRes.body.id;
  console.log(`   subsystemId: ${subsystemId}`);

  // 3. Create a test harvest
  console.log("3. Creating test harvest...");
  const harvestRes = await httpPost("/api/harvests", {
    sourcePath: "tests/crossref-spawnplan-integration.test.ts",
    sourceFilename: "crossref-spawnplan-integration.test.ts",
    model: "test",
    totalCandidates: 1,
    candidates: [
      {
        title: "CrossRef Test Candidate",
        intentDescription: "Intent: verify cross_reference creation on plan linking.",
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

  // 4. Create an unlinked candidate
  console.log("4. Creating unlinked candidate...");
  const candidateRes = await httpPost("/api/harvest-candidates", {
    harvestId,
    title: "CrossRef Test Candidate",
    intentDescription: "Intent: verify cross_reference creation on plan linking.",
    tags: ["test"],
  });
  assert(candidateRes.status === 201, `Created candidate → ${candidateRes.status}`);
  const candidateId: string = candidateRes.body.id;
  console.log(`   candidateId: ${candidateId}`);

  // ════════════════════════════════════════════════════════════
  //  PART A: PATCH with planRef
  // ════════════════════════════════════════════════════════════

  const PLAN_REF_PATCH = "9998";

  console.log(`\n--- PART A: PATCH link with planRef=${PLAN_REF_PATCH} ---\n`);

  // 5. Verify no cross-reference exists yet
  console.log("5. Verifying no cross-reference before link...");
  const xrefBefore = await httpGet(
    `/api/cross-references?sourceType=harvest_candidate&sourceId=${candidateId}`
  );
  assert(xrefBefore.status === 200, "GET cross-references succeeds");
  const beforeCount = Array.isArray(xrefBefore.body?.items) ? xrefBefore.body.items.length : 0;
  assert(beforeCount === 0, `No cross-references before linking (count: ${beforeCount})`);

  // 6. PATCH candidate with systemId + planRef
  console.log("6. Linking candidate via PATCH with planRef...");
  const patchRes = await httpPatch(`/api/harvest-candidates/${candidateId}`, {
    systemId,
    planRef: PLAN_REF_PATCH,
  });
  assert(patchRes.status === 200, `PATCH succeeded → ${patchRes.status}`);
  assert(patchRes.body.system_id === systemId, "candidate.system_id is set");

  // 7. Verify cross-reference now exists
  console.log("7. Verifying cross-reference after PATCH...");
  const xrefAfter = await httpGet(
    `/api/cross-references?sourceType=harvest_candidate&sourceId=${candidateId}`
  );
  assert(xrefAfter.status === 200, "GET cross-references succeeds");
  const xrefs: any[] = Array.isArray(xrefAfter.body?.items) ? xrefAfter.body.items : [];
  assert(xrefs.length >= 1, `Cross-reference exists (count: ${xrefs.length})`);

  const planXref = xrefs.find(
    (x: any) =>
      x.source_type === "harvest_candidate" &&
      x.source_id === candidateId &&
      x.target_type === "plan" &&
      x.target_id === PLAN_REF_PATCH &&
      x.rel_type === "ag:spawns_plan"
  );
  assert(!!planXref, "Cross-reference has correct fields (source=harvest_candidate, target=plan, rel=ag:spawns_plan)");
  console.log(`   crossRefId: ${planXref.id}`);
  console.log(`   rel_type: ${planXref.rel_type}`);
  console.log(`   target_id: ${planXref.target_id}`);

  // 8. Verify reverse lookup returns the candidate
  console.log(`8. Verifying reverse lookup GET /api/plans/${PLAN_REF_PATCH}/candidates...`);
  const revLookup = await httpGet(`/api/plans/${PLAN_REF_PATCH}/candidates`);
  assert(revLookup.status === 200, "GET plan candidates succeeds");
  assert(revLookup.body.planRef === PLAN_REF_PATCH, "Response includes correct planRef");
  assert(revLookup.body.total >= 1, `At least 1 candidate returned (count: ${revLookup.body.total})`);
  const foundCandidate = (revLookup.body.items || []).find((c: any) => c.id === candidateId);
  assert(!!foundCandidate, "Our candidate appears in the reverse lookup results");
  console.log(`   found: id=${foundCandidate.id}, title="${foundCandidate.title}"`);

  // 9. Verify idempotency — PATCH again with same planRef should NOT create a duplicate
  console.log("9. Verifying idempotency (PATCH again with same planRef)...");
  await httpPatch(`/api/harvest-candidates/${candidateId}`, {
    planRef: PLAN_REF_PATCH,
  });
  const xrefAfterDup = await httpGet(
    `/api/cross-references?sourceType=harvest_candidate&sourceId=${candidateId}`
  );
  const planXrefs = (Array.isArray(xrefAfterDup.body?.items) ? xrefAfterDup.body.items : []).filter(
    (x: any) =>
      x.target_type === "plan" &&
      x.target_id === PLAN_REF_PATCH &&
      x.rel_type === "ag:spawns_plan"
  );
  assert(planXrefs.length === 1, `No duplicate cross-reference created (count: ${planXrefs.length})`);

  // ════════════════════════════════════════════════════════════
  //  PART B: PATCH with a SECOND planRef (multiple plans)
  // ════════════════════════════════════════════════════════════

  const PLAN_REF_SECOND = "9997";

  console.log(`\n--- PART B: PATCH link with second planRef=${PLAN_REF_SECOND} ---\n`);

  // 10. PATCH with a different planRef
  console.log("10. Linking candidate to a second plan via PATCH...");
  await httpPatch(`/api/harvest-candidates/${candidateId}`, {
    planRef: PLAN_REF_SECOND,
  });

  // 11. Verify BOTH cross-references exist
  console.log("11. Verifying both cross-references exist...");
  const xrefBoth = await httpGet(
    `/api/cross-references?sourceType=harvest_candidate&sourceId=${candidateId}`
  );
  const bothPlanXrefs = (Array.isArray(xrefBoth.body?.items) ? xrefBoth.body.items : []).filter(
    (x: any) => x.target_type === "plan" && x.rel_type === "ag:spawns_plan"
  );
  assert(bothPlanXrefs.length === 2, `Both plan cross-references exist (count: ${bothPlanXrefs.length})`);
  const hasFirst = bothPlanXrefs.some((x: any) => x.target_id === PLAN_REF_PATCH);
  const hasSecond = bothPlanXrefs.some((x: any) => x.target_id === PLAN_REF_SECOND);
  assert(hasFirst, `First plan (${PLAN_REF_PATCH}) cross-reference exists`);
  assert(hasSecond, `Second plan (${PLAN_REF_SECOND}) cross-reference exists`);

  // 12. Verify reverse lookup for the second plan
  console.log(`12. Verifying reverse lookup for plan ${PLAN_REF_SECOND}...`);
  const revLookup2 = await httpGet(`/api/plans/${PLAN_REF_SECOND}/candidates`);
  assert(revLookup2.status === 200, "GET second plan candidates succeeds");
  assert(revLookup2.body.total >= 1, "Second plan has candidates");
  const foundInSecond = (revLookup2.body.items || []).find((c: any) => c.id === candidateId);
  assert(!!foundInSecond, "Our candidate appears in the second plan's lookup");

  // ════════════════════════════════════════════════════════════
  //  PART C: spawn-plan endpoint
  // ════════════════════════════════════════════════════════════

  const PLAN_REF_SPAWN = "9996";

  console.log(`\n--- PART C: spawn-plan endpoint with planRef=${PLAN_REF_SPAWN} ---\n`);

  // 13. Create a fresh candidate for spawn-plan test
  console.log("13. Creating fresh candidate for spawn-requirement test...");
  const freshCandRes = await httpPost("/api/harvest-candidates", {
    harvestId,
    title: "SpawnPlan Test Candidate",
    intentDescription: "Intent: verify the spawn-requirement endpoint creates requirement + cross-reference.",
    tags: ["test", "spawn-plan"],
  });
  assert(freshCandRes.status === 201, `Created fresh candidate → ${freshCandRes.status}`);
  const freshCandidateId: string = freshCandRes.body.id;
  console.log(`   candidateId: ${freshCandidateId}`);

  // 14. POST spawn-requirement
  console.log("14. Calling spawn-requirement endpoint...");
  const spawnRes = await httpPost(`/api/harvest-candidates/${freshCandidateId}/spawn-requirement`, {
    systemId,
    subsystemId,
    planRef: PLAN_REF_SPAWN,
    priority: "High",
    status: "ToDo",
  });
  assert(spawnRes.status === 201, `spawn-requirement succeeded → ${spawnRes.status}`);
  assert(spawnRes.body.candidate !== undefined, "Response includes candidate");
  assert(spawnRes.body.requirement !== undefined, "Response includes requirement");
  assert(spawnRes.body.crossReference !== null, "Response includes crossReference");
  console.log(`   requirement.id: ${spawnRes.body.requirement.id}`);
  console.log(`   requirement.title: "${spawnRes.body.requirement.title}"`);
  console.log(`   requirement.priority: ${spawnRes.body.requirement.priority}`);
  console.log(`   crossReference.relType: ${spawnRes.body.crossReference.relType}`);

  // 15. Verify spawn-plan cross-reference exists
  console.log(`15. Verifying spawn-requirement cross-reference via GET /api/cross-references...`);
  const xrefSpawn = await httpGet(
    `/api/cross-references?sourceType=harvest_candidate&sourceId=${freshCandidateId}`
  );
  const spawnXref = (Array.isArray(xrefSpawn.body?.items) ? xrefSpawn.body.items : []).find(
    (x: any) =>
      x.target_type === "plan" &&
      x.target_id === PLAN_REF_SPAWN &&
      x.rel_type === "ag:spawns_plan"
  );
  assert(!!spawnXref, "spawn-requirement cross-reference exists in cross-references API");
  console.log(`   crossRefId: ${spawnXref.id}`);

  // 16. Verify reverse lookup for spawn-plan ref
  console.log(`16. Verifying reverse lookup for plan ${PLAN_REF_SPAWN}...`);
  const revLookupSpawn = await httpGet(`/api/plans/${PLAN_REF_SPAWN}/candidates`);
  assert(revLookupSpawn.status === 200, "GET spawn plan candidates succeeds");
  const foundInSpawn = (revLookupSpawn.body.items || []).find((c: any) => c.id === freshCandidateId);
  assert(!!foundInSpawn, "Spawn candidate appears in plan reverse lookup");

  // 17. Verify requirement was created correctly
  console.log("17. Verifying the created requirement...");
  const reqRes = await httpGet(`/api/requirements?systemId=${systemId}&subsystemId=${subsystemId}`);
  assert(reqRes.status === 200, "GET requirements succeeds");
  const reqs: any[] = Array.isArray(reqRes.body?.items) ? reqRes.body.items : [];
  const ourReq = reqs.find((r: any) => r.id === spawnRes.body.requirement.id);
  assert(!!ourReq, "Our requirement exists in the requirements list");
  assert(ourReq.title === "SpawnPlan Test Candidate", "Requirement title matches candidate title");
  assert(ourReq.priority === "High", "Requirement priority is High");
  assert(ourReq.status === "ToDo", "Requirement status is ToDo");
  assert(ourReq.systemId === systemId, "Requirement systemId is correct");
  assert(ourReq.subsystemId === subsystemId, "Requirement subsystemId is correct");

  // ════════════════════════════════════════════════════════════
  //  CLEANUP
  // ════════════════════════════════════════════════════════════

  console.log("\n--- CLEANUP ---\n");
  console.log("18. Cleaning up test data...");

  // Delete cross-references we created
  for (const xref of [...bothPlanXrefs, spawnXref].filter(Boolean)) {
    if (xref?.id) await httpDelete(`/api/cross-references/${xref.id}`);
  }
  console.log("    Cross-references deleted.");

  // Delete the spawn-plan requirement
  if (spawnRes.body.requirement?.id) {
    await httpDelete(`/api/requirements/${spawnRes.body.requirement.id}`);
  }
  console.log("    Requirement deleted.");

  // Delete candidates
  await httpDelete(`/api/harvest-candidates/${candidateId}`);
  await httpDelete(`/api/harvest-candidates/${freshCandidateId}`);
  console.log("    Candidates deleted.");

  // Delete harvest
  await httpDelete(`/api/harvests/${harvestId}`);
  console.log("    Harvest deleted.");

  // Delete subsystem and system
  await httpDelete(`/api/subsystems/${subsystemId}`);
  await httpDelete(`/api/systems/${systemId}`);
  console.log("    System hierarchy deleted.");

  console.log("\n✅ All tests passed!");
  process.exit(0);
}

main().catch((err) => {
  console.error("\n❌ Test failed:", err.message);
  process.exit(1);
});
