#!/usr/bin/env npx tsx
/**
 * Service-tier integration test: Ruling 8 read-side case-insensitive tag
 * matching against a REAL PostgreSQL + live nebula-srv.
 *
 * Runs in the service-test-gates workflow's nebula-srv job (throwaway
 * PostgreSQL 17 + live server on :3101). Hermetic tier is src/tagFilter.test.ts.
 *
 * Proves, through the public API only:
 *   1. POST persists case-variant tags verbatim (no write-side mutation).
 *   2. GET /agent-records?tag=to:dba (single) delivers to:DBA + to:dba records.
 *   3. GET /agent-records?tag=to:dba,area:inbox (multi AND) is case-insensitive.
 *   4. POST /agent-records/search match=any (OR) is case-insensitive.
 *   5. AND still excludes non-matching tags; OR matches any case variant.
 *   6. Cleanup: DELETE both fixtures (verifies the DELETE endpoint too).
 *
 * Usage: npx tsx tests/tag-filter-ci.integration.test.ts   (server on :3101)
 */

import * as http from 'http';

const BASE = process.env.NEBULA_TEST_BASE || 'http://localhost:3101';
const NS = `r8readcase-${Date.now()}`;

function httpReq(method: string, path: string, body?: unknown): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const url = new URL(path, BASE);
    const payload = body === undefined ? undefined : JSON.stringify(body);
    const req = http.request(
      {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname + url.search,
        method,
        headers: payload ? { 'Content-Type': 'application/json', 'Content-Length': String(Buffer.byteLength(payload)) } : {},
      },
      (res) => {
        let data = '';
        res.on('data', (c: string) => (data += c));
        res.on('end', () => {
          try {
            resolve({ status: res.statusCode!, body: JSON.parse(data) });
          } catch {
            reject(new Error(`non-JSON ${res.statusCode} from ${path}: ${data.slice(0, 200)}`));
          }
        });
      },
    );
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function expect(cond: boolean, label: string, detail?: unknown): Promise<void> {
  if (!cond) throw new Error(`FAIL: ${label}${detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 400) : ''}`);
  console.log(`  ok: ${label}`);
}

interface Fixture {
  id: string;
  title: string;
  tags: string[];
}

async function insert(tags: string[], title: string): Promise<Fixture> {
  const res = await httpReq('POST', '/api/agent-records', {
    recordType: 'engineering_log',
    role: 'dba',
    title,
    content: `Ruling 8 read-side integration fixture ${NS} — safe to delete`,
    tags,
  });
  if (res.status !== 201) throw new Error(`POST failed ${res.status}: ${JSON.stringify(res.body).slice(0, 200)}`);
  return { id: res.body.id, title, tags };
}

async function main(): Promise<void> {
  console.log(`service-tier integration: case-insensitive tag filter (ns=${NS}) against ${BASE}`);

  // ── fixtures: case-variant tags, distinct titles ──────────────────
  const upper = await insert(['to:DBA', 'area:inbox'], `${NS} upper`);
  const lower = await insert(['to:dba', 'area:inbox'], `${NS} lower`);
  const mixed = await insert(['to:Dba'], `${NS} mixed`);
  const control = await insert(['to:planner'], `${NS} control`);
  const created: Fixture[] = [upper, lower, mixed, control];

  try {
    // 1. Write-side must be verbatim — the fix is read-side only.
    const roundTrip = await httpReq('GET', `/api/agent-records?id=${upper.id}`);
    await expect(
      JSON.stringify(roundTrip.body.items?.[0]?.tags ?? roundTrip.body.tags) === JSON.stringify(['to:DBA', 'area:inbox']),
      'write-side verbatim: stored tags keep their original case',
      roundTrip.body,
    );

    // 2. Single-tag GET delivers ALL case variants; exact-match would find 1.
    const single = await httpReq('GET', `/api/agent-records?tag=to:dba&pageSize=500&includeContent=false`);
    const singleIds: string[] = (single.body.items ?? []).map((r: any) => r.id);
    await expect(singleIds.includes(upper.id), 'single to:dba delivers to:DBA record');
    await expect(singleIds.includes(lower.id), 'single to:dba delivers to:dba record');
    await expect(singleIds.includes(mixed.id), 'single to:dba delivers to:Dba record');
    await expect(!singleIds.includes(control.id), 'single to:dba excludes control');

    // 3. Multi-tag AND, cross-case against stored case.
    const andQ = await httpReq('GET', `/api/agent-records?tag=to:DBA,AREA:INBOX&pageSize=500&includeContent=false`);
    const andIds: string[] = (andQ.body.items ?? []).map((r: any) => r.id);
    await expect(andIds.includes(upper.id) && andIds.includes(lower.id), 'AND to:DBA+AREA:INBOX delivers both case variants');
    await expect(!andIds.includes(mixed.id), 'AND excludes record lacking area:inbox');
    await expect(!andIds.includes(control.id), 'AND excludes control');

    // 4. POST /agent-records/search, OR semantics, cross-case.
    const search = await httpReq('POST', '/api/agent-records/search', {
      tags: ['TO:DBA', 'TO:PLANNER'],
      match: 'any',
      limit: 500,
    });
    await expect(search.status === 200, 'search endpoint answers 200', search.status);
    const searchRows: any[] = search.body.records ?? search.body.items ?? search.body.rows ?? [];
    const searchIds = searchRows.map((r) => r.id);
    await expect(searchIds.includes(upper.id) && searchIds.includes(lower.id) && searchIds.includes(mixed.id), 'OR TO:DBA delivers all three variants');
    await expect(searchIds.includes(control.id), 'OR TO:PLANNER still reaches the control record');

    // 5. Search AND semantics stays conjunctive under the new clause.
    const searchAnd = await httpReq('POST', '/api/agent-records/search', {
      tags: ['to:dba', 'AREA:INBOX'],
      match: 'all',
      limit: 500,
    });
    const andRows: any[] = searchAnd.body.records ?? searchAnd.body.items ?? searchAnd.body.rows ?? [];
    const andIds2 = andRows.map((r) => r.id);
    await expect(andIds2.includes(upper.id) && andIds2.includes(lower.id), 'search AND cross-case delivers both area:inbox fixtures');
    await expect(!andIds2.includes(mixed.id), 'search AND excludes mixed (no area:inbox)');

    // 6. Negative control: unrelated tag matches none of the fixtures.
    const neg = await httpReq('GET', `/api/agent-records?tag=to:nobody&pageSize=500&includeContent=false`);
    const negIds: string[] = (neg.body.items ?? []).map((r: any) => r.id);
    await expect(!created.some((f) => negIds.includes(f.id)), 'negative control: to:nobody matches none');
  } finally {
    // ── cleanup: delete fixtures; failure to clean is reported, not swallowed ──
    for (const f of created) {
      const del = await httpReq('DELETE', `/api/agent-records/${f.id}`);
      if (del.status !== 200) console.error(`  WARN: cleanup of ${f.id} returned ${del.status}`);
    }
    const probe = await httpReq('GET', `/api/agent-records?search=${encodeURIComponent(NS)}&pageSize=500&includeContent=false`);
    const leftovers: string[] = (probe.body.items ?? []).map((r: any) => r.id);
    if (leftovers.length > 0) throw new Error(`FAIL: cleanup left ${leftovers.length} fixture(s) behind`);
    console.log('  ok: cleanup verified (0 fixtures remain)');
  }

  console.log('PASS: case-insensitive tag filter (Ruling 8 read-side) verified end-to-end');
}

main().catch((e: Error) => {
  console.error(e.message);
  process.exit(1);
});
