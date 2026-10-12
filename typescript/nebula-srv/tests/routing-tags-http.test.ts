/**
 * Integration test: POST /api/agent-records rejects undeliverable routing tags.
 *
 * Background (DBA inspection 26a6acb3): the inbox read path matches the
 * routing tag EXACTLY as `to:<role.toLowerCase()>`. A record written with an
 * uppercase routing tag such as `to:DBA` therefore matches NO inbox query,
 * for either spelling — it is silently undeliverable. The Supervisor's own
 * ruling (24162dba, answering ac9fc4af) was tagged `to:DBA` and stayed
 * undelivered for ~6.5 hours.
 *
 * The unit suite (src/routing-tags.test.ts) pins the pure validator, but it
 * does NOT prove the handler calls it: with the guard deleted from routes.ts
 * the unit suite still passes 51/51. This file covers the wiring — that the
 * guard is actually reachable from the canonical write path.
 *
 * SELF-SEEDED: creates its own rows under unique titles and deletes them
 * afterwards, so it does not depend on live database contents.
 *
 * Verifies:
 *  1. uppercase routing tag => 400 naming the tag and the corrected form
 *  2. the rejected record was NOT persisted (no phantom row)
 *  3. lowercase routing tag => 201, persisted verbatim
 *  4. non-routing prefixes (type:, status:) are NOT policed
 *  5. missing tags => 201
 *  6. every offender is reported, not only the first
 *
 * Usage: npx tsx tests/routing-tags-http.test.ts
 * Requires a reachable nebula-srv (BASE below) on the target database.
 */

import * as http from "http";

const BASE = process.env.NEBULA_TEST_BASE || "http://localhost:3101";

// Fixture titles are prefixed with MARK_PREFIX and carry a per-run unique
// marker. Two reasons, both learned the hard way:
//
//   1. Cleanup runs only on the success path. An aborted run (assert fired)
//      leaves fixtures behind, and a naive "no fixture rows exist" assertion
//      then fails on the NEXT run for a reason unrelated to the code.
//      The sweep below clears those before asserting.
//   2. The not-persisted check must see only THIS run's rows, or a leftover
//      from a previous run reads as a phantom write.
const MARK_PREFIX = "routing-tags-http fixture";
const RUN = `${MARK_PREFIX} #${Date.now()}`;

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

const created: any[] = [];

async function create(body: any): Promise<{ status: number; body: any }> {
  const res = await httpReq("POST", "/api/agent-records", body);
  if (res.status === 201 && res.body?.id) created.push(res.body.id);
  return res;
}

/** Every record whose title carries the fixture prefix (any run). */
async function fixtureRows(): Promise<any[]> {
  const res = await httpReq("GET", "/api/agent-records?search=routing-tags-http&limit=500");
  const items: any[] = Array.isArray(res.body?.items) ? res.body.items : [];
  return items.filter((r) => String(r.title || "").startsWith(MARK_PREFIX));
}

/**
 * Clear fixtures orphaned by an earlier aborted run. Without this the
 * not-persisted assertion below trips on someone else's leftovers, which is
 * a false failure that hides the real signal.
 */
async function sweepStaleFixtures(): Promise<void> {
  const stale = await fixtureRows();
  for (const r of stale) await httpReq("DELETE", `/api/agent-records/${r.id}`);
  if (stale.length) console.log(`  · swept ${stale.length} stale fixture(s) from an aborted run`);
}

async function run() {
  console.log(`routing-tags-http integration test against ${BASE}`);

  // Start from a clean slate so an earlier aborted run cannot masquerade as
  // a phantom write below.
  await sweepStaleFixtures();

  // ── 1. The exact defect: uppercase routing tag is rejected ──────────
  const upper = await create({
    recordType: "report",
    role: "DBA",
    title: `${RUN}: uppercase routing tag must be rejected`,
    content: "Self-seeded fixture for tests/routing-tags-http.test.ts",
    tags: ["to:DBA", "type:report"],
    level: 1,
  });
  assert("uppercase to:DBA => 400", upper.status === 400, `got ${upper.status}`);
  assert(
    "error names the offending tag",
    typeof upper.body?.error === "string" && upper.body.error.includes("to:DBA"),
    JSON.stringify(upper.body).slice(0, 200));
  assert(
    "error suggests the deliverable form",
    typeof upper.body?.error === "string" && upper.body.error.includes("to:dba"),
    JSON.stringify(upper.body).slice(0, 200));

  // ── 2. A rejected write must leave no row behind ────────────────────
  // Scoped to THIS run's marker: the uppercase fixture is the only record
  // created so far, so any row carrying this run's marker is a phantom.
  const mine = (await fixtureRows()).filter((r) => String(r.title || "").startsWith(RUN));
  assert(
    "rejected record was NOT persisted",
    mine.length === 0,
    `a rejected write left ${mine.length} row(s) in the database`);

  // ── 3. Canonical lowercase routing tag still works ──────────────────
  const lower = await create({
    recordType: "report",
    role: "DBA",
    title: `${RUN}: lowercase routing tag is accepted`,
    content: "Self-seeded fixture for tests/routing-tags-http.test.ts",
    tags: ["to:dba", "type:report"],
    level: 1,
  });
  assert("lowercase to:dba => 201", lower.status === 201, `got ${lower.status}`);
  assert(
    "tag persisted verbatim (not rewritten)",
    Array.isArray(lower.body?.tags) && lower.body.tags.includes("to:dba"),
    JSON.stringify(lower.body?.tags));

  // ── 4. Non-routing prefixes are NOT policed ─────────────────────────
  const nonRouting = await create({
    recordType: "report",
    role: "engineer",
    title: `${RUN}: non-routing prefixes untouched`,
    content: "Uppercase outside to: must not be policed",
    tags: ["type:Report", "status:DONE"],
    level: 1,
  });
  assert("uppercase non-routing tags => 201", nonRouting.status === 201, `got ${nonRouting.status}`);

  // ── 5. Missing tags => 201 (no routing claimed) ─────────────────────
  const noTags = await create({
    recordType: "report",
    role: "planner",
    title: `${RUN}: no tags is fine`,
    content: "No tags supplied at all",
    level: 1,
  });
  assert("absent tags => 201", noTags.status === 201, `got ${noTags.status}`);

  // ── 6. Every offender is reported, not just the first ───────────────
  const multi = await create({
    recordType: "report",
    role: "DBA",
    title: `${RUN}: multiple offenders`,
    content: "Two uppercase routing tags in one write",
    tags: ["to:DBA", "ok", "to:Architect"],
    level: 1,
  });
  assert("two bad routing tags => 400", multi.status === 400, `got ${multi.status}`);
  assert(
    "both offenders named in one message",
    typeof multi.body?.error === "string" &&
      multi.body.error.includes("to:DBA") &&
      multi.body.error.includes("to:Architect"),
    JSON.stringify(multi.body).slice(0, 300));

  // ── Cleanup ────────────────────────────────────────────────────────
  for (const id of created) {
    await httpReq("DELETE", `/api/agent-records/${id}`);
  }
  console.log(`  ✓ ${created.length} fixtures cleaned up`);

  console.log("routing-tags-http integration test: ALL PASS");
  process.exit(0);
}

run().catch((err) => {
  console.error(`  ✗ FAIL: ${err.message}`);
  process.exit(1);
});