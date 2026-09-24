/**
 * Integration test: GET /api/attestations?pr=<N> — exact, indexed
 * attestation lookup for merge-gate gate 3 (bin/merge_pr.py).
 *
 * Verifies against real agent-record data (read-only):
 *  1. 200 + items[] with role=tester, exact pr:<N> tag, attestation shape
 *     (canonical: assessment + type:approval + status:done; legacy:
 *     type:attestation) — tester finding 84ca2388's rule, server-side
 *  2. newest-first ordering
 *  3. known canonical rows resolve: pr:487 -> 7a668bd1, pr:491 -> b47510d6
 *  4. validation: missing/garbage pr => 400
 *  5. regression: tester records that merely MENTION a PR (intent row
 *     b8acd611 for pr:492, finding 84ca2388) never satisfy the shape
 *
 * Usage: npx tsx tests/attestations-lookup.test.ts
 * Requires a reachable nebula-srv (BASE below) on the target database.
 */

import * as http from "http";

const BASE = process.env.NEBULA_TEST_BASE || "http://localhost:3111";

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

  // 1. canonical row for pr:491
  const r491 = await httpReq("GET", "/api/attestations?pr=491");
  assert("pr=491 returns 200", r491.status === 200, `got ${r491.status}`);
  assert("pr=491 items is an array", Array.isArray(r491.body.items));
  assert("pr=491 finds the canonical attestation b47510d6",
    r491.body.items.some((r: any) => String(r.id).startsWith("b47510d6")),
    JSON.stringify(r491.body.items).slice(0, 200));
  for (const r of r491.body.items) {
    assert(`pr=491 row ${String(r.id).slice(0, 8)} is tester-shaped`, r.role === "tester" && isAttestationShaped(r));
  }

  // 2. canonical row for pr:487
  const r487 = await httpReq("GET", "/api/attestations?pr=487");
  assert("pr=487 finds the canonical attestation 7a668bd1",
    r487.body.items.some((r: any) => String(r.id).startsWith("7a668bd1")));

  // 3. newest-first ordering
  const times = r487.body.items.map((r: any) => r.createdAt as number);
  assert("pr=487 ordered newest-first", times.every((t: number, i: number) => i === 0 || times[i - 1] >= t));

  // 4. regression: intent/finding rows for pr:492 must NOT satisfy the shape.
  // (pr:492 legitimately HAS an attestation — re-attestation row 36b47e81 at
  // head 1a9bfd77, per architect ruling f8e82dba — so assert shape purity,
  // not emptiness: every returned row must be attestation-shaped.)
  const r492 = await httpReq("GET", "/api/attestations?pr=492");
  assert("pr=492 returns 200", r492.status === 200);
  for (const r of r492.body.items) {
    assert(`pr=492 row ${String(r.id).slice(0, 8)} is attestation-shaped`, isAttestationShaped(r),
      JSON.stringify(r).slice(0, 200));
  }
  assert("pr=492 excludes marker-less mentions (b8acd611 intent, 84ca2388 finding)",
    !r492.body.items.some((r: any) =>
      String(r.id).startsWith("b8acd611") || String(r.id).startsWith("84ca2388")),
    JSON.stringify(r492.body.items).slice(0, 200));
  assert("pr=492 finds the re-attestation 36b47e81 (head 1a9bfd77)",
    r492.body.items.some((r: any) => String(r.id).startsWith("36b47e81")));

  // 5. exact tag match: no cross-PR leakage
  const r48 = await httpReq("GET", "/api/attestations?pr=48");
  assert("pr=48 exact-tag match finds nothing (no #48/#487 confusion)",
    Array.isArray(r48.body.items) && r48.body.items.length === 0,
    JSON.stringify(r48.body.items).slice(0, 200));

  // 6. validation
  const bad = await httpReq("GET", "/api/attestations");
  assert("missing pr => 400", bad.status === 400, `got ${bad.status}`);
  const garbage = await httpReq("GET", "/api/attestations?pr=abc");
  assert("non-integer pr => 400", garbage.status === 400, `got ${garbage.status}`);
  const negative = await httpReq("GET", "/api/attestations?pr=-3");
  assert("negative pr => 400", negative.status === 400, `got ${negative.status}`);

  console.log("attestations-lookup integration test: ALL PASS");
  process.exit(0);
}

run().catch((err) => {
  console.error(`  ✗ FAIL: ${err.message}`);
  process.exit(1);
});
