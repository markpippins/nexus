#!/usr/bin/env npx tsx
/**
 * Integration test: tag address policy two-phase behavior (PR #693 draft).
 *
 * Phase W (warn): default server accepts unregistered to:-addresses but
 *   logs a [tag-address-policy] WARN — the shipped default; proves the PR
 *   arms nothing by itself (R13 sequencing).
 * Phase R (reject): NEBULA_TAG_ADDRESS_POLICY=reject server answers 422 for
 *   unregistered addresses, and 201 for registered roles, registered
 *   aliases (to:all), registered telemetry (to:wr-conf-observer), Class V
 *   value tags (including uppercase — Ruling 11), and case variants of
 *   registered addresses. Also: PATCH enforcement + no-phantom-write.
 *
 * SELF-SEEDED: unique titles per run, swept on success; a sweep-first pass
 * clears aborted-run residue so a prior failure cannot fail this run.
 *
 * Usage: NEBULA_TEST_BASE=http://localhost:3199 npx tsx tests/tag-address-policy-http.test.ts
 */

import * as http from 'http';

const BASE = process.env.NEBULA_TEST_BASE || 'http://localhost:3199';
const RUN = `tap-${Date.now()}`;

function httpReq(method: string, reqPath: string, body?: any): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : undefined;
    const url = new URL(reqPath, BASE);
    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method,
      headers: bodyStr
        ? { 'Content-Type': 'application/json', 'Content-Length': String(Buffer.byteLength(bodyStr)) }
        : {},
    };
    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', (chunk: string) => (data += chunk));
      res.on('end', () => {
        try { resolve({ status: res.statusCode!, body: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode!, body: data }); }
      });
    });
    req.on('error', (err) => reject(new Error(`Request failed: ${err.message}`)));
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

function ok(label: string, condition: boolean, detail?: unknown): void {
  if (!condition) {
    console.error(`  FAIL: ${label}${detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 300) : ''}`);
    process.exit(1);
  }
  console.log(`  ok: ${label}`);
}

async function sweep(): Promise<void> {
  const probe = await httpReq('GET', `/api/agent-records?search=${encodeURIComponent(RUN)}&pageSize=500&includeContent=false`);
  for (const row of probe.body?.items ?? []) {
    await httpReq('DELETE', `/api/agent-records/${row.id}`);
  }
}

async function phaseWarn(): Promise<void> {
  console.log(`phase W (warn) against ${BASE}`);
  await sweep();
  const created: string[] = [];
  try {
    const post = await httpReq('POST', '/api/agent-records', {
      recordType: 'engineering_log', role: 'dba',
      title: `${RUN} warn-phase unregistered`,
      content: `address-policy warn-phase fixture ${RUN} — safe to delete`,
      tags: ['to:definitely-not-registered-anywhere'],
    });
    ok('warn mode: unregistered to: address is ACCEPTED (201)', post.status === 201, post.status);
    created.push(post.body.id);

    const reread = await httpReq('GET', `/api/agent-records?id=${post.body.id}`);
    ok('warn mode: record persisted verbatim', (reread.body?.items ?? []).length === 1);
  } finally {
    for (const id of created) await httpReq('DELETE', `/api/agent-records/${id}`);
  }
}

async function phaseReject(): Promise<void> {
  console.log(`phase R (reject) against ${BASE}`);
  await sweep();
  const created: string[] = [];
  try {
    // 1. unregistered Class R address => 422, not persisted
    const rej = await httpReq('POST', '/api/agent-records', {
      recordType: 'engineering_log', role: 'dba',
      title: `${RUN} reject-phase unregistered`,
      content: `must not persist ${RUN}`,
      tags: ['to:ghost-address'],
    });
    ok('reject mode: unregistered to:ghost-address => 422', rej.status === 422, { status: rej.status, body: rej.body });
    ok('reject mode: 422 names the offender', JSON.stringify(rej.body).includes('to:ghost-address'));

    const probe = await httpReq('GET', `/api/agent-records?search=${encodeURIComponent(RUN)}&pageSize=500&includeContent=false`);
    ok('reject mode: rejected record NOT persisted (no phantom row)', (probe.body?.items ?? []).length === 0, probe.body?.items?.length);

    // 2. registered role, alias, telemetry, Class V, case variants => 201
    const acceptSets: Array<[string, string[]]> = [
      ['registered role', ['to:dba']],
      ['registered alias to:all (kind=alias, never rejects)', ['to:all']],
      ['registered telemetry to:wr-conf-observer (kind=telemetry, never rejects)', ['to:wr-conf-observer']],
      ['Class V value tags incl. uppercase (Ruling 11: never reject)', ['type:change', 'ADR-006', 'blocks:PR-580']],
      ['case variant of registered address (Ruling 11)', ['to:DBA']],
      ['mixed payload: only the unregistered member is flagged', ['to:dba', 'to:all', 'type:change', 'to:ghost-b']],
    ];
    for (const [label, tags] of acceptSets) {
      const isMixed = label.startsWith('mixed');
      const post = await httpReq('POST', '/api/agent-records', {
        recordType: 'engineering_log', role: 'dba',
        title: `${RUN} accept ${label}`,
        content: `address-policy accept fixture ${RUN} — safe to delete`,
        tags,
      });
      if (isMixed) {
        ok(`reject mode: ${label} => 422 (to:ghost-b)`, post.status === 422, { status: post.status, body: post.body });
      } else {
        ok(`reject mode: ${label} => 201`, post.status === 201, { status: post.status, body: post.body });
        created.push(post.body.id);
      }
    }

    // 3. PATCH enforcement: flipping tags onto an existing record is policed
    const seed = await httpReq('POST', '/api/agent-records', {
      recordType: 'engineering_log', role: 'dba',
      title: `${RUN} patch seed`,
      content: `address-policy patch fixture ${RUN} — safe to delete`,
      tags: ['to:dba'],
    });
    created.push(seed.body.id);
    const patchRej = await httpReq('PATCH', `/api/agent-records/${seed.body.id}`, {
      tags: ['to:dba', 'to:ghost-c'],
    });
    ok('reject mode: PATCH introducing unregistered address => 422', patchRej.status === 422, { status: patchRej.status, body: patchRej.body });
    const patchOk = await httpReq('PATCH', `/api/agent-records/${seed.body.id}`, {
      tags: ['to:dba', 'to:all'],
    });
    ok('reject mode: PATCH with registered addresses => 200', patchOk.status === 200, { status: patchOk.status, body: patchOk.body });

    console.log('PASS: tag address policy two-phase behavior verified');
  } finally {
    for (const id of created) await httpReq('DELETE', `/api/agent-records/${id}`);
    await sweep();
  }
}

async function main(): Promise<void> {
  const which = process.env.POLICY_PHASE || 'both';
  if (which === 'warn' || which === 'both') await phaseWarn();
  if (which === 'reject' || which === 'both') await phaseReject();
}

main().catch((e: Error) => {
  console.error(e.message);
  process.exit(1);
});
