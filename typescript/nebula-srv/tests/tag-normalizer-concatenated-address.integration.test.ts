#!/usr/bin/env npx tsx
/**
 * Service-tier integration test: R13 step-2 concatenated-address split-repair,
 * INDEPENDENTLY gated.
 *
 * ── Why this is a separate file, not another case in tag-normalizer ────────
 *
 * That suite's `expect()` THROWS on first failure, and its assertion 2
 * (`to:DBA stored lowercase (Class R)`) ALWAYS fails on a server without the
 * write-side normalizer. The R13 step-2 assertion is therefore structurally
 * unreachable as a negative control: the full suite cannot demonstrate that it
 * is capable of failing. Measured against unpatched live :3101 (PR #714):
 *
 *   FAIL: to:DBA stored lowercase (Class R) ::
 *     ["  to:DBA  ","[\"area:inbox\",...","to:engineer-to:engineer-ii",...]
 *   ok: POST returns a tags array          <- only one ok before the abort
 *
 * i.e. assertions 3..13 never execute. That is a real hole in the attestation
 * trail for the split-repair: nothing in the service tier independently gates
 * it against an unpatched server, which is the exact failure this change exists
 * to prevent.
 *
 * This file closes it by sending ONLY concatenated-address defects, one record
 * per scenario, so no unrelated defect can mask the assertion under test. It
 * also ACCUMULATES failures rather than throwing on the first, so one broken
 * scenario reports alongside the others instead of hiding them.
 *
 * Scenarios cover the split itself (two-way, three-way, case-mixed, bare `to:`
 * segment, dedupe), the PATCH write path, delivery through a split address,
 * and two non-interference guarantees (an already-clean address is not
 * re-split; a Class V tag merely CONTAINING `to:` mid-string is not split).
 *
 * Usage:
 *   NEBULA_TEST_BASE=http://localhost:3101 \
 *     npx tsx tests/tag-normalizer-concatenated-address.integration.test.ts
 */

import * as http from 'http';

const BASE = process.env.NEBULA_TEST_BASE || 'http://localhost:3101';
// Deliberately NOT `norm-…`: that is the sibling suite's prefix, and a title
// containing `norm-<ts>` would match the sibling's cleanup sweep as a substring
// if the two ever ran inside the same millisecond.
const NS = `caddr-${Date.now()}`;

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

/** How many `to:` occurrences a tag holds. >1 means still concatenated. */
function toBoundaries(tag: string): number {
  return tag.toLowerCase().split('to:').length - 1;
}

let failures = 0;

/** Accumulate, never throw: one broken scenario must not mask the others. */
function check(cond: boolean, label: string, detail?: unknown): void {
  if (!cond) {
    failures++;
    console.error(`  FAIL: ${label}${detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 300) : ''}`);
  } else {
    console.log(`  ok: ${label}`);
  }
}

async function main(): Promise<void> {
  console.log(`concatenated-address split-repair (ns=${NS}) against ${BASE}`);
  const created: Array<{ id: string }> = [];

  /** POST one fixture with exactly the tags under test, nothing else. */
  const post = async (label: string, tags: string[]): Promise<{ id: string; tags: string[] }> => {
    const res = await httpReq('POST', '/api/agent-records', {
      recordType: 'engineering_log',
      role: 'dba',
      title: `${NS} ${label}`,
      content: `concatenated-address fixture ${NS}/${label} — safe to delete`,
      tags,
    });
    if (res.status !== 201) {
      throw new Error(`POST ${label} failed: ${res.status} ${JSON.stringify(res.body).slice(0, 200)}`);
    }
    created.push({ id: res.body.id });
    return { id: res.body.id as string, tags: res.body.tags as string[] };
  };

  const noConcatenationSurvives = (tags: string[]): boolean => tags.every((t) => toBoundaries(t) <= 1);

  try {
    // ── S1: the canonical R13 step-2 defect, two-way ──
    const s1 = await post('two-way', ['to:engineer-to:engineer-ii']);
    check(
      s1.tags.includes('to:engineer') && s1.tags.includes('to:engineer-ii'),
      'S1 two-way concat split into two addresses',
      s1.tags,
    );
    check(noConcatenationSurvives(s1.tags), 'S1 no tag retains a second to: boundary', s1.tags);
    check(s1.tags.length === 2, 'S1 produced exactly two tags (no residue)', s1.tags);

    // ── S2: three-way concat — every later to: starts a new address ──
    const s2 = await post('three-way', ['to:engineer-to:engineer-ii-to:tester']);
    check(
      s2.tags.includes('to:engineer') && s2.tags.includes('to:engineer-ii') && s2.tags.includes('to:tester'),
      'S2 three-way concat split into three addresses',
      s2.tags,
    );
    check(s2.tags.length === 3, 'S2 produced exactly three tags (no residue)', s2.tags);

    // ── S3: case-mixed concat — split THEN lowercase (§4.2d before §4.3) ──
    const s3 = await post('case-mixed', ['to:Engineer-to:Engineer-II']);
    check(
      s3.tags.includes('to:engineer') && s3.tags.includes('to:engineer-ii'),
      'S3 case-mixed concat split and lowercased',
      s3.tags,
    );

    // ── S4: already-clean address must NOT be re-split (no over-split) ──
    const s4 = await post('clean', ['to:engineer']);
    check(
      s4.tags.length === 1 && s4.tags[0] === 'to:engineer',
      'S4 clean single address stored unchanged',
      s4.tags,
    );

    // ── S5: hyphenated role key keeps its own internal hyphens ──
    const s5 = await post('hyphenated', ['to:engineer-ii']);
    check(
      s5.tags.length === 1 && s5.tags[0] === 'to:engineer-ii',
      'S5 hyphenated role not split at its hyphen',
      s5.tags,
    );

    // ── S6: bare `to:` segment is unrecoverable and dropped, rest kept ──
    //     `to:-to:engineer` → segments "to:"(len 3, dropped) + "to:engineer"
    const s6 = await post('bare-to', ['to:-to:engineer']);
    check(
      s6.tags.includes('to:engineer') && !s6.tags.includes('to:'),
      'S6 bare to: segment dropped, surviving address kept',
      s6.tags,
    );

    // ── S7: split halves that are identical dedupe to one address ──
    const s7 = await post('dedupe', ['to:engineer-to:engineer']);
    check(
      s7.tags.length === 1 && s7.tags[0] === 'to:engineer',
      'S7 identical halves dedupe to one address',
      s7.tags,
    );

    // ── S8: Class V non-interference — a VALUE tag merely CONTAINING `to:`
    //     does not start with it, so it is neither split nor lowercased. ──
    const s8 = await post('classv', ['note-to:Engineer']);
    check(
      s8.tags.length === 1 && s8.tags[0] === 'note-to:Engineer',
      'S8 Class V tag containing to: mid-string is neither split nor lowercased',
      s8.tags,
    );

    // ── S9: the PATCH write path applies the same split-repair ──
    const s9 = await post('patch-target', ['to:engineer']);
    const patch = await httpReq('PATCH', `/api/agent-records/${s9.id}`, {
      tags: ['to:tester-to:planner'],
    });
    check(patch.status === 200, 'S9 PATCH answers 200', patch.status);
    const reread = await httpReq('GET', `/api/agent-records?id=${s9.id}`);
    const patchedTags: string[] = reread.body.items?.[0]?.tags ?? [];
    check(
      patchedTags.includes('to:tester') && patchedTags.includes('to:planner'),
      'S9 PATCH split-repairs the concatenated address',
      patchedTags,
    );

    // ── S10: a SPLIT address actually delivers — the point of the repair ──
    //      (the corpus defect was a mis-delivery, not a cosmetic one) ──
    const s10 = await post('delivery', ['to:engineer-to:engineer-ii']);
    const s10id = s10.id;
    const bySplit = await httpReq(
      'GET',
      `/api/agent-records?tag=${encodeURIComponent('to:engineer-ii')}&pageSize=500&includeContent=false`,
    );
    const ids: string[] = (bySplit.body.items ?? []).map((r: any) => r.id);
    check(
      ids.includes(s10id),
      'S10 record delivers via the second split address (to:engineer-ii)',
      { found: ids.includes(s10id) },
    );

    // ── S11: nothing concatenated may survive ANY scenario above ──
    //      Sweep every stored fixture rather than trusting per-case checks. ──
    const rereadAll = await httpReq('GET', `/api/agent-records?search=${encodeURIComponent(NS)}&pageSize=500`);
    const allTags: string[] = (rereadAll.body.items ?? []).flatMap((r: any) => (r.tags ?? []) as string[]);
    check(
      allTags.length > 0 && allTags.every((t) => toBoundaries(t) <= 1),
      'S11 no fixture retained a concatenated address',
      allTags.filter((t) => toBoundaries(t) > 1),
    );
    check(
      allTags.every((t) => t.trim() === t && t !== ''),
      'S12 no fixture retained an untrimmed or empty tag',
      allTags.filter((t) => t.trim() !== t || t === ''),
    );
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

  if (failures > 0) {
    console.error(`FAIL: ${failures} concatenated-address assertion(s) failed against ${BASE}`);
    process.exit(1);
  }
  console.log('PASS: R13 step-2 concatenated-address split-repair verified end-to-end');
}

main().catch((e: Error) => {
  console.error(e.message);
  process.exit(1);
});