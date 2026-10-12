#!/usr/bin/env npx tsx
/**
 * Service-tier integration test: Ruling 8 read-side case-insensitive tag
 * matching against a REAL PostgreSQL + live nebula-srv.
 *
 * Runs in the service-test-gates workflow's nebula-srv job (throwaway
 * PostgreSQL 17 + live server on :3101). Hermetic tier is src/tagFilter.test.ts.
 *
 * PROVES, through the public API only:
 *   1. POST REJECTS a case-variant ROUTING tag (`to:DBA`) — Ruling 38J. The
 *      write side no longer stores a mis-cased routing address, because inbox
 *      lookup matches `to:`+role.toLowerCase() exactly and the uppercase
 *      spelling reaches no inbox (DBA inspection 26a6acb3).
 *   2. POST still persists case-variant NON-ROUTING tags verbatim (no write-side
 *      mutation for tags that carry no delivery semantics) — Ruling 8.
 *   3. GET /agent-records?tag=... (single) delivers non-routing case variants.
 *   4. GET /agent-records?tag=a,b (multi AND) is case-insensitive.
 *   5. POST /agent-records/search match=any (OR) is case-insensitive.
 *   6. AND still excludes non-matching tags; OR matches any case variant.
 *   7. Cleanup: DELETE both fixtures (verifies the DELETE endpoint too).
 *
 * RE-SCOPED by Ruling 38J (was: "POST persists case-variant tags verbatim,
 * no write-side mutation" — true when read-side lowercasing was the only
 * defence). Ruling 8 read-side case-insensitivity is UNCHANGED and still
 * proven here; what changed is that the write side now refuses to create the
 * uppercase ROUTING fixtures this suite used to seed, because doing so would
 * reintroduce the undeliverable-address defect. Uppercase routing tags are
 * asserted as REJECTED (1) rather than stored-then-matched.
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

  // ── Ruling 38J: an uppercase ROUTING tag is now REJECTED at POST ──────
  // The uppercase routing fixtures this suite used to seed can no longer be
  // created — that is the point of 38J. Assert the rejection so the write-side
  // contract stays pinned.
  for (const bad of [['to:DBA'], ['to:Dba'], ['TO:PLANNER']]) {
    const res = await httpReq('POST', '/api/agent-records', {
      recordType: 'engineering_log',
      role: 'dba',
      title: `${NS} uppercase routing must be rejected`,
      content: `Ruling 38J rejection fixture ${NS} — never stored`,
      tags: bad,
    });
    await expect(res.status === 400, `POST rejects uppercase routing tag ${JSON.stringify(bad)}`, res.status);
    await expect(
      typeof res.body?.error === 'string' && res.body.error.includes('to:'),
      `400 for ${JSON.stringify(bad)} names the routing tag`, JSON.stringify(res.body),
    );
  }

  // ── fixtures: case-variant NON-ROUTING tags ──────────────────────────
  // Ruling 8 read-side case-insensitivity is unchanged and still proven here;
  // only the write-side handling of ROUTING tags moved (38J). `area:` carries no
  // delivery semantics, so its case is preserved verbatim and a case-insensitive
  // read must find it regardless of the query's case.
  // AND fixtures carry a second DISTINCT tag. `allTagsClause` (tagFilter.ts:61)
  // counts tag ELEMENTS matching ANY lowered query term and requires that count
  // to equal the number of query TERMS. Since matching is case-insensitive,
  // `AREA:INBOX` and `area:inbox` are the SAME value — so a 2-term AND made of
  // two case-variants of one tag is unsatisfiable by a 1-tag record. `ns:and`
  // gives the conjunction a real second term while the cross-case property
  // still rides on the `area:` term.
  const upper = await insert(['area:Inbox', 'ns:and'], `${NS} upper`);
  const lower = await insert(['area:inbox', 'ns:and'], `${NS} lower`);
  const mixed = await insert(['AREA:INBOX'], `${NS} mixed`);
  const control = await insert(['area:Outbox'], `${NS} control`);
  const created: Fixture[] = [upper, lower, mixed, control];

  try {
    // 1. Write-side verbatim for non-routing tags — the Ruling 8 contract.
    const roundTrip = await httpReq('GET', `/api/agent-records?id=${upper.id}`);
    await expect(
      JSON.stringify(roundTrip.body.items?.[0]?.tags ?? roundTrip.body.tags) === JSON.stringify(['area:Inbox', 'ns:and']),
      'write-side verbatim: non-routing tag keeps its original case',
      roundTrip.body,
    );

    // 2. Single-tag GET delivers ALL case variants; exact-match would find 1.
    const single = await httpReq('GET', `/api/agent-records?tag=area:inbox&pageSize=500&includeContent=false`);
    const singleIds: string[] = (single.body.items ?? []).map((r: any) => r.id);
    await expect(singleIds.includes(upper.id), 'single area:inbox delivers area:Inbox record');
    await expect(singleIds.includes(lower.id), 'single area:inbox delivers area:inbox record');
    await expect(singleIds.includes(mixed.id), 'single area:inbox delivers AREA:INBOX record');
    await expect(!singleIds.includes(control.id), 'single area:inbox excludes control');

    // 3. Multi-tag AND, cross-case against stored case.
    const andQ = await httpReq('GET', `/api/agent-records?tag=AREA:INBOX,ns:and&pageSize=500&includeContent=false`);
    const andIds: string[] = (andQ.body.items ?? []).map((r: any) => r.id);
    await expect(andIds.includes(upper.id) && andIds.includes(lower.id), 'AND AREA:INBOX+ns:and delivers both area: case variants');
    await expect(!andIds.includes(control.id), 'AND excludes control');

    // 4. POST /agent-records/search, OR semantics, cross-case.
    const search = await httpReq('POST', '/api/agent-records/search', {
      tags: ['AREA:INBOX', 'area:outbox'],
      match: 'any',
      limit: 500,
    });
    await expect(search.status === 200, 'search endpoint answers 200', search.status);
    const searchRows: any[] = search.body.records ?? search.body.items ?? search.body.rows ?? [];
    const searchIds = searchRows.map((r) => r.id);
    await expect(searchIds.includes(upper.id) && searchIds.includes(lower.id) && searchIds.includes(mixed.id), 'OR AREA:INBOX delivers all three variants');
    await expect(searchIds.includes(control.id), 'OR area:outbox still reaches the control record');

    // 5. Search AND semantics stays conjunctive under the new clause.
    const searchAnd = await httpReq('POST', '/api/agent-records/search', {
      tags: ['area:inbox'],
      match: 'all',
      limit: 500,
    });
    const andRows: any[] = searchAnd.body.records ?? searchAnd.body.items ?? searchAnd.body.rows ?? [];
    const andIds2 = andRows.map((r) => r.id);
    await expect(andIds2.includes(upper.id) && andIds2.includes(lower.id), 'search AND cross-case delivers both area:inbox fixtures');
    await expect(!andIds2.includes(control.id), 'search AND excludes control');

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
