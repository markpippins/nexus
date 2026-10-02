#!/usr/bin/env npx tsx
/**
 * Service-tier integration test: write-side tag normalizer (ratified card §4).
 *
 * Against a PATCHED server, a dirty tags payload must be stored normalized;
 * against the UNPATCHED server (negative control), the first assertion fails —
 * proving the test detects the defect it exists for.
 *
 * Usage: NEBULA_TEST_BASE=http://localhost:3101 npx tsx tests/tag-normalizer.integration.test.ts
 */

import * as http from 'http';

const BASE = process.env.NEBULA_TEST_BASE || 'http://localhost:3101';
const NS = `norm-${Date.now()}`;

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
        headers: payload
          ? { 'Content-Type': 'application/json', 'Content-Length': String(Buffer.byteLength(payload)) }
          : {},
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
  if (!cond) throw new Error(`FAIL: ${label}${detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 300) : ''}`);
  console.log(`  ok: ${label}`);
}

async function main(): Promise<void> {
  console.log(`tag normalizer integration (ns=${NS}) against ${BASE}`);
  const created: Array<{ id: string }> = [];

  try {
    // ── POST with a maximally dirty payload (all §4 repairs at once) ──
    const post = await httpReq('POST', '/api/agent-records', {
      recordType: 'engineering_log',
      role: 'dba',
      title: `${NS} dirty write`,
      content: `tag normalizer integration fixture ${NS} — safe to delete`,
      tags: [
        '  to:DBA  ',                                    // trim + Class R lowercase
        JSON.stringify(['area:inbox', '"type:change"']), // JSON-unwrap + strip quotes
        'affects:pr:613,pr:614,pr:615',                  // comma-split
        'to:engineer-to:engineer-ii',                    // §4.2d concat split-repair (R13 step 2)
        'blocks:PR-580',                                 // Class V case preserved
        '',                                              // dropped
      ],
    });
    if (post.status !== 201) throw new Error(`POST failed: ${post.status} ${JSON.stringify(post.body).slice(0, 200)}`);
    created.push({ id: post.body.id });

    const stored: string[] = post.body.tags;
    await expect(Array.isArray(stored), 'POST returns a tags array');
    await expect(stored.includes('to:dba'), 'to:DBA stored lowercase (Class R)', stored);
    await expect(stored.includes('area:inbox'), 'JSON-unwrapped tag stored', stored);
    await expect(stored.includes('type:change'), 'quote-stripped tag stored', stored);
    await expect(stored.includes('affects:pr:613') && stored.includes('pr:614') && stored.includes('pr:615'),
      'comma-joined list stored as three tags', stored);
    await expect(stored.includes('to:engineer') && stored.includes('to:engineer-ii'),
      'concatenated to: address split-repaired into two addresses (R13 step 2)', stored);
    await expect(stored.includes('blocks:PR-580'), 'Class V case PRESERVED (blocks:PR-580)', stored);
    await expect(!stored.some((t: string) => t.includes(',') || /[\[\]{}"]/.test(t) || t.trim() === ''),
      'no commas/brackets/empties survive', stored);

    // ── delivery still works post-normalization (to:dba lowercase) ──
    const deliv = await httpReq(
      'GET',
      `/api/agent-records?tag=${encodeURIComponent('to:dba')}&pageSize=500&includeContent=false`,
    );
    const ids: string[] = (deliv.body.items ?? []).map((r: any) => r.id);
    await expect(ids.includes(created[0].id), 'normalized record delivers via canonical to:dba query');

    // ── PATCH path normalizes too ──
    const patch = await httpReq('PATCH', `/api/agent-records/${created[0].id}`, {
      tags: ['  TO:tester ', '"patched-flag"'],
    });
    await expect(patch.status === 200, 'PATCH answers 200', patch.status);
    const reread = await httpReq('GET', `/api/agent-records?id=${created[0].id}`);
    const patchedTags: string[] = reread.body.items?.[0]?.tags ?? [];
    await expect(patchedTags.includes('to:tester'), 'PATCH stored to:tester lowercase', patchedTags);
    await expect(patchedTags.includes('patched-flag'), 'PATCH stored quote-stripped tag', patchedTags);

    console.log('PASS: write-side tag normalizer verified end-to-end');
  } finally {
    for (const f of created) {
      const del = await httpReq('DELETE', `/api/agent-records/${f.id}`);
      if (del.status !== 200) console.error(`  WARN: cleanup ${f.id} -> ${del.status}`);
    }
    const probe = await httpReq('GET', `/api/agent-records?search=${encodeURIComponent(NS)}&pageSize=500&includeContent=false`);
    const left: string[] = (probe.body.items ?? []).map((r: any) => r.id);
    if (left.length > 0) throw new Error(`FAIL: cleanup left ${left.length} fixture(s)`);
    console.log('  ok: cleanup verified (0 fixtures remain)');
  }
}

main().catch((e: Error) => {
  console.error(e.message);
  process.exit(1);
});
