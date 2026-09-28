/**
 * Integration test: GET /api/attestations?pr=<N> — exact, indexed
 * attestation lookup for merge-gate gate 3 (bin/merge_pr.py).
 *
 * SELF-SEEDED (fresh-DB CI): the endpoint is a pure function of seeded
 * agent_records, so the test creates its own tester-shaped rows under unique
 * PR tags and cleans up after itself. It does not depend on live database
 * contents, which made the original version unrunnable on a fresh database.
 *
 * Verifies:
 *  1. 200 + items[] with role=tester, exact pr:<N> tag match, attestation
 *     shape (canonical: assessment + type:approval + status:done; legacy:
 *     type:attestation) — tester finding 84ca2388's rule, server-side
 *  2. newest-first ordering
 *  3. regression: records that merely MENTION the PR (wrong shape / wrong
 *     role) never satisfy the lookup
 *  4. exact tag match: no cross-PR leakage
 *  5. validation: missing/garbage/negative pr => 400
 *
 * Usage: npx tsx tests/attestations-lookup.test.ts
 * Requires a reachable nebula-srv (BASE below) on the target database.
 */

import * as http from "http";

const BASE = process.env.NEBULA_TEST_BASE || "http://localhost:3101";

// Unique PR tags for this run — never collide with real attestation rows.
const PR_MAIN = "491911";
const PR_MENTION = "491912";
const PR_ABSENT = "491913";

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

function isAttestationShaped(r: any): boolean {
  const tags: string[] = r.tags || [];
  if (tags.includes("type:attestation")) return true;
  return (
    r.recordType === "assessment" &&
    tags.includes("type:approval") &&
    tags.includes("status:done")
  );
}

async function run() {
  console.log(`attestations-lookup integration test against ${BASE}`);

  // ── Seed fixtures ────────────────────────────────────────────────
  // Canonical-shaped attestation (assessment + type:approval + status:done)
  // and a legacy-shaped one (type:attestation), both tester-role, pr:491911.
  const canonical = await httpReq("POST", "/api/agent-records", {
    recordType: "assessment",
    role: "tester",
    title: "attestations-lookup fixture: canonical shape",
    content: "Self-seeded fixture for tests/attestations-lookup.test.ts",
    tags: [`pr:${PR_MAIN}`, "type:approval", "status:done"],
    level: 1,
  });
  assert("canonical fixture created", canonical.status === 201, `got ${canonical.status}`);

  const legacy = await httpReq("POST", "/api/agent-records", {
    recordType: "report",
    role: "tester",
    title: "attestations-lookup fixture: legacy shape",
    content: "Self-seeded legacy-shape fixture (type:attestation)",
    tags: [`pr:${PR_MAIN}`, "type:attestation"],
    level: 1,
  });
  assert("legacy fixture created", legacy.status === 201, `got ${legacy.status}`);

  // A row that merely MENTIONS the PR: wrong role and not attestation-shaped.
  // It must never satisfy the lookup (regression from finding 84ca2388).
  const mention = await httpReq("POST", "/api/agent-records", {
    recordType: "analysis",
    role: "engineer",
    title: "attestations-lookup fixture: mention, wrong shape",
    content: "Mentions pr without attestation shape — must be excluded",
    tags: [`pr:${PR_MENTION}`, "type:approval"],
    level: 1,
  });
  assert("mention fixture created", mention.status === 201, `got ${mention.status}`);

  // 1. canonical + legacy rows resolve for the seeded PR
  const rMain = await httpReq("GET", `/api/attestations?pr=${PR_MAIN}`);
  assert(`pr=${PR_MAIN} returns 200`, rMain.status === 200, `got ${rMain.status}`);
  assert(`pr=${PR_MAIN} items is an array`, Array.isArray(rMain.body.items));
  assert(`pr=${PR_MAIN} finds the canonical attestation`,
    rMain.body.items.some((r: any) => String(r.id).startsWith(canonical.body.id)),
    JSON.stringify(rMain.body.items).slice(0, 200));
  assert(`pr=${PR_MAIN} finds the legacy attestation`,
    rMain.body.items.some((r: any) => String(r.id).startsWith(legacy.body.id)));
  for (const r of rMain.body.items) {
    assert(`pr=${PR_MAIN} row ${String(r.id).slice(0, 8)} is tester-shaped`,
      r.role === "tester" && isAttestationShaped(r));
  }

  // 2. newest-first ordering
  const times = rMain.body.items.map((r: any) => r.createdAt as number);
  assert("ordered newest-first",
    times.every((t: number, i: number) => i === 0 || times[i - 1] >= t));

  // 3. regression: mention-only rows never satisfy the shape
  const rMention = await httpReq("GET", `/api/attestations?pr=${PR_MENTION}`);
  assert(`pr=${PR_MENTION} returns 200`, rMention.status === 200);
  assert(`pr=${PR_MENTION} excludes non-attestation mentions`,
    Array.isArray(rMention.body.items) && rMention.body.items.length === 0,
    JSON.stringify(rMention.body.items).slice(0, 200));

  // 4. exact tag match: no cross-PR leakage
  const rAbsent = await httpReq("GET", `/api/attestations?pr=${PR_ABSENT}`);
  assert(`pr=${PR_ABSENT} exact-tag match finds nothing (no #48/#487 confusion)`,
    Array.isArray(rAbsent.body.items) && rAbsent.body.items.length === 0,
    JSON.stringify(rAbsent.body.items).slice(0, 200));

  // 5. validation
  const bad = await httpReq("GET", "/api/attestations");
  assert("missing pr => 400", bad.status === 400, `got ${bad.status}`);
  const garbage = await httpReq("GET", "/api/attestations?pr=abc");
  assert("non-integer pr => 400", garbage.status === 400, `got ${garbage.status}`);
  const negative = await httpReq("GET", "/api/attestations?pr=-3");
  assert("negative pr => 400", negative.status === 400, `got ${negative.status}`);

  // ── Cleanup ──────────────────────────────────────────────────────
  for (const id of [canonical.body.id, legacy.body.id, mention.body.id]) {
    await httpReq("DELETE", `/api/agent-records/${id}`);
  }
  console.log("  ✓ fixtures cleaned up");

  console.log("attestations-lookup integration test: ALL PASS");
  process.exit(0);
}

run().catch((err) => {
  console.error(`  ✗ FAIL: ${err.message}`);
  process.exit(1);
});
