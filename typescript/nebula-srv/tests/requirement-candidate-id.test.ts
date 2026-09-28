/**
 * Integration test: requirements.candidate_id population through promotion flows.
 *
 * Regression for to-do e68449f2 ("Populate requirements.candidate_id through
 * promotion flow + backfill"). The completion chain candidate→requirement→plan→WR
 * is only as good as requirements.candidate_id; this test pins the write path.
 *
 * Test flow:
 *  1. Seed a system + subsystem + harvest + unlinked candidate
 *  2. POST /api/harvest-candidates/:id/spawn-requirement → REQUIREMENT MUST
 *     carry candidate_id = candidate.id (the regression; verb renamed from
 *     spawn-plan per decision 319defa5)
 *  3. GET /api/requirements/:id → hydrated candidateId at read parity
 *  4. POST /api/harvest-candidates/promote-to-plan → 410 Gone with pointer to
 *     the canonical spawn-requirement path (architect retirement ruling, e68449f2 A2)
 *  5. Clean up
 *
 * Usage: npx tsx tests/requirement-candidate-id.test.ts   (nebula-srv on :3101)
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
      timeout: 15000,
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
    req.on("timeout", () => { req.destroy(new Error(`request timed out: ${method} ${path}`)); });
    req.on("error", reject);
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

function httpPost(path: string, body?: any) { return httpReq("POST", path, body); }
function httpGet(path: string) { return httpReq("GET", path); }
function httpDelete(path: string) { return httpReq("DELETE", path); }

let failures = 0;
function assert(condition: boolean, msg: string): void {
  if (!condition) { failures++; throw new Error(`FAIL: ${msg}`); }
  console.log(`  ✓ ${msg}`);
}

// ── Main Test ─────────────────────────────────────────────────────

async function main() {
  console.log("=== requirement.candidate_id promotion regression test ===\n");

  let systemId = "";
  let subsystemId = "";
  let harvestId = "";
  let candidateId = "";

  try {
    // 1. Seed hierarchy
    console.log("1. Seeding test system/subsystem...");
    const sysRes = await httpPost("/api/systems", {
      name: "CandId Regression System",
      description: "Temporary system for candidate_id regression testing",
    });
    assert(sysRes.status === 201, `Created system → ${sysRes.status}`);
    systemId = sysRes.body.id;

    const subRes = await httpPost("/api/subsystems", {
      systemId,
      name: "CandId Regression Subsystem",
    });
    assert(subRes.status === 201, `Created subsystem → ${subRes.status}`);
    subsystemId = subRes.body.id;

    // 2. Seed harvest + candidate
    console.log("2. Seeding test harvest + candidate...");
    const harvestRes = await httpPost("/api/harvests", {
      sourcePath: "tests/requirement-candidate-id.test.ts",
      sourceFilename: "requirement-candidate-id.test.ts",
      model: "test",
      totalCandidates: 1,
      candidates: [{
        title: "CandId Regression Candidate",
        intentDescription: "Intent: verify requirements.candidate_id write path.",
        implementationNotes: [],
        codeSnippets: [],
        openQuestions: [],
        tags: ["test"],
      }],
    });
    assert(harvestRes.status === 201, `Created harvest → ${harvestRes.status}`);
    harvestId = harvestRes.body.id;

    const candidateRes = await httpPost("/api/harvest-candidates", {
      harvestId,
      title: "CandId Regression Candidate",
      intentDescription: "Intent: verify requirements.candidate_id write path.",
      tags: ["test"],
    });
    assert(candidateRes.status === 201, `Created candidate → ${candidateRes.status}`);
    candidateId = candidateRes.body.id;

    // 3. THE REGRESSION: promote via canonical spawn-plan flow
    console.log("3. Promoting candidate via POST /spawn-requirement...");
    const spawnRes = await httpPost(`/api/harvest-candidates/${candidateId}/spawn-requirement`, {
      systemId,
      subsystemId,
      status: "Backlog",
    });
    assert(spawnRes.status === 201, `spawn-requirement succeeded → ${spawnRes.status}`);
    const requirementId: string | undefined = spawnRes.body?.requirement?.id;
    assert(!!requirementId, "spawn-plan returned a requirement");
    assert(
      spawnRes.body.requirement.candidateId === candidateId,
      `REGRESSION CHECK: requirement.candidateId === candidate (${spawnRes.body.requirement.candidateId} vs ${candidateId})`
    );

    // 4. Read parity
    console.log("4. Verifying read surface hydrates candidateId...");
    const getRes = await httpGet(`/api/requirements/${requirementId}`);
    assert(getRes.status === 200, `GET requirement → ${getRes.status}`);
    assert(
      (getRes.body?.candidateId ?? getRes.body?.candidate_id) === candidateId,
      "GET /api/requirements/:id returns candidateId at parity"
    );

    // 5. Retired route fails loudly
    console.log("5. Verifying promote-to-plan is retired with pointer...");
    const retireRes = await httpPost("/api/harvest-candidates/promote-to-plan", {
      candidateIds: [candidateId],
    });
    assert(retireRes.status === 410, `promote-to-plan → 410 Gone (got ${retireRes.status})`);
    assert(
      retireRes.body?.useInstead === "POST /api/harvest-candidates/:id/spawn-requirement",
      "410 body points at canonical spawn-requirement path"
    );

    // Cleanup
    console.log("\n6. Cleaning up fixtures...");
    if (requirementId) await httpDelete(`/api/requirements/${requirementId}`);
    await httpDelete(`/api/harvest-candidates/${candidateId}`);
    await httpDelete(`/api/harvests/${harvestId}`);
    await httpDelete(`/api/subsystems/${subsystemId}`);
    await httpDelete(`/api/systems/${systemId}`);
    console.log("  ✓ cleanup done");
  } catch (err) {
    // best-effort cleanup on failure so failed runs don't litter
    if (candidateId) await httpDelete(`/api/harvest-candidates/${candidateId}`);
    if (harvestId) await httpDelete(`/api/harvests/${harvestId}`);
    if (subsystemId) await httpDelete(`/api/subsystems/${subsystemId}`);
    if (systemId) await httpDelete(`/api/systems/${systemId}`);
    console.error(err instanceof Error ? err.message : err);
    process.exit(1);
  }

  if (failures > 0) process.exit(1);
  console.log("\n=== ALL ASSERTIONS PASSED ===");
}

main();
