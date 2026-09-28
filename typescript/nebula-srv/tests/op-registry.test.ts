/**
 * Integration test: Op Mapping Registry CRUD + lifecycle operations.
 *
 * Tests:
 *  1. POST /api/op-registry — create an entry (success + validation error)
 *  2. GET /api/op-registry — list all entries (with filters)
 *  3. GET /api/op-registry/:id — get single entry
 *  4. PATCH /api/op-registry/:id/deprecate — deprecate an entry
 *  5. POST /api/op-registry/fork — create new version of an entry
 *  6. GET /api/op-registry/:id/lineage — show version lineage
 *  7. DELETE /api/op-registry/:id — soft-delete an entry
 *
 * Usage: npx ts-node tests/op-registry.test.ts
 */

import * as http from "http";

const BASE = "http://localhost:3101";

function httpReq(method: string, path: string, body?: any): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : undefined;
    const url = new URL(path, BASE);
    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + (body ? "" : url.search),
      method,
      headers: bodyStr
        ? { "Content-Type": "application/json", "Content-Length": String(Buffer.byteLength(bodyStr)) }
        : {},
    };
    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk: string) => (data += chunk));
      res.on("end", () => {
        try { resolve({ status: res.statusCode!, body: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode!, body: data }); }
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

async function run() {
  console.log("\nOp Registry Integration Tests\n" + "─".repeat(40));

  // ── Test 1: Create an entry ─────────────────────────────────────
  const entryId = "TEST_SCAFFOLD_SERVICE:v1";
  const entry = await httpReq("POST", "/api/op-registry", {
    id: entryId,
    intent_id: "TEST_SCAFFOLD_SERVICE",
    version: "v1",
    label: "Test: Scaffold a new service",
    match_patterns: [
      "scaffold a (new )?(?<serviceType>\\w+) service",
      "create a (new )?(?<serviceType>\\w+) service",
    ],
    opcode_template: [
      { op: "CREATE_DIR", target: "spec/service", params: { specType: "service" } },
      { op: "WRITE_FILE", target: "files/scaffold", params: { template: "service-scaffold" } },
    ],
    required_params: ["serviceName", "serviceType"],
    optional_params: ["port", "framework"],
    preconditions: ["spec/service exists"],
    postconditions: ["service scaffold generated"],
    idempotency_key: "scaffold-{serviceName}",
    notes: "Test entry for integration tests",
  });
  assert("Create entry returns 201", entry.status === 201, String(entry.status));
  assert("Entry has correct id", entry.body.id === entryId);
  assert("Entry has active status", entry.body.status === "active");

  // ── Test 2: List entries ────────────────────────────────────────
  const listAll = await httpReq("GET", "/api/op-registry");
  assert("List entries returns 200", listAll.status === 200);
  assert("List contains our entry", (listAll.body.items || []).some((e: any) => e.id === entryId));

  const listFiltered = await httpReq("GET", "/api/op-registry?intent_id=TEST_SCAFFOLD_SERVICE");
  assert("Filter by intent_id works", listFiltered.status === 200);
  assert("Filter returns exactly 1", (listFiltered.body.items || []).length === 1, String(listFiltered.body.total));

  const listSearch = await httpReq("GET", "/api/op-registry?search=Scaffold");
  assert("Text search works", listSearch.status === 200 && (listSearch.body.items || []).length >= 1, String(listSearch.body.total));

  // ── Test 3: Get single entry ────────────────────────────────────
  const getEntry = await httpReq("GET", `/api/op-registry/${encodeURIComponent(entryId)}`);
  assert("Get entry returns 200", getEntry.status === 200);
  assert("Entry id matches", getEntry.body.id === entryId);

  // ── Test 4: Deprecate ───────────────────────────────────────────
  const deprecated = await httpReq("PATCH", `/api/op-registry/${encodeURIComponent(entryId)}/deprecate`);
  assert("Deprecate returns 200", deprecated.status === 200);
  assert("Status is deprecated", deprecated.body.status === "deprecated");

  // Re-activate via direct update for fork test (test 5)
  const reactivate = await httpReq("PATCH", `/api/op-registry/${encodeURIComponent(entryId)}/deprecate`, {
    successor_id: undefined,
  });
  // We don't have a way to go back to 'active', so let's directly update.
  // Actually — make a different entry for fork test
  const forkSourceId = "TEST_FORK_SOURCE:v1";
  const forkSource = await httpReq("POST", "/api/op-registry", {
    id: forkSourceId,
    intent_id: "TEST_FORK_SOURCE",
    version: "v1",
    label: "Fork source test",
    match_patterns: ["test fork pattern"],
    opcode_template: [{ op: "READ_FILE", target: "source/main" }],
    required_params: ["input"],
  });
  assert("Fork source created", forkSource.status === 201);

  const forkResult = await httpReq("POST", "/api/op-registry/fork", {
    source_id: forkSourceId,
    new_version: "v2",
    label: "Fork target v2",
    notes: "Updated template",
    opcode_template: [{ op: "READ_FILE", target: "source-v2/main" }],
  });
  assert("Fork returns 201", forkResult.status === 201, String(forkResult.status));
  assert("Fork has new id", forkResult.body.fork.id === "TEST_FORK_SOURCE:v2");
  assert("Source is superseded", forkResult.body.superseded === forkSourceId);

  // ── Test 5: Lineage ─────────────────────────────────────────────
  const lineage = await httpReq("GET", `/api/op-registry/${encodeURIComponent(forkSourceId)}/lineage`);
  assert("Lineage returns 200", lineage.status === 200);
  assert("Lineage contains both versions", lineage.body.count === 2, String(lineage.body.count));
  assert("Includes v2 entry", (lineage.body.entries || []).some((e: any) => e.version === "v2"));

  // ── Test 6: Soft-delete ─────────────────────────────────────────
  const deleted = await httpReq("DELETE", `/api/op-registry/${encodeURIComponent(entryId)}`);
  assert("Delete returns 200", deleted.status === 200);
  assert("Deleted confirmed", deleted.body.deleted === true);

  const getDeleted = await httpReq("GET", `/api/op-registry/${encodeURIComponent(entryId)}`);
  assert("Deleted entry returns 404", getDeleted.status === 404);

  // ── Test 7: ISA validation — invalid opcode should fail ─────────
  // The trigger validates that opcode_template entries use valid ISA verbs
  const badEntry = await httpReq("POST", "/api/op-registry", {
    id: "TEST_INVALID_OPCODE:v1",
    intent_id: "TEST_INVALID_OPCODE",
    opcode_template: [{ op: "INVALID_OPCODE_XYZ", target: "foo" }],
  });
  assert("Invalid opcode returns 422 or 500", [422, 500].includes(badEntry.status), String(badEntry.status));
  if (badEntry.status === 500) {
    assert("Error mentions Invalid opcode",
      (badEntry.body?.error || "").includes("Invalid opcode"), String(badEntry.body?.error));
  }
  assert("Entry was NOT created with invalid id",
    badEntry.body?.id !== "TEST_INVALID_OPCODE:v1");

  // ── Cleanup ─────────────────────────────────────────────────────
  await httpReq("DELETE", "/api/op-registry/TEST_FORK_SOURCE:v1");
  await httpReq("DELETE", "/api/op-registry/TEST_FORK_SOURCE:v2");

  console.log("\n" + "✓".repeat(20));
  console.log("All op-registry integration tests passed!\n");
}

run().catch((err) => {
  console.error("Test runner failed:", err);
  process.exit(1);
});
