#!/usr/bin/env npx tsx
/**
 * Integration test: tag address policy two-phase behavior.
 *
 * Phase W (warn): against the live server the CI job boots (default
 *   http://localhost:3101 — the same convention as routing-tags-http.test.ts).
 *   Proves the shipped default arms nothing: an unregistered to:-address is
 *   accepted and persisted, with a [tag-address-policy] WARN logged.
 * Phase R (reject): the same handler with NEBULA_TAG_ADDRESS_POLICY=reject.
 *   A server's policy mode is fixed at boot, so one process cannot serve both
 *   phases. This phase SPAWNS its own server on an isolated port against the
 *   same database and tears it down in a finally block.
 *
 *   Before 2026-11-06 the whole file defaulted to :3199 and assumed an operator
 *   had patched a server there by hand. In CI nothing listened on :3199, so the
 *   gate failed with a bare "Request failed: " (the connection error carried no
 *   detail) — a test that needs a server must start that server, otherwise the
 *   port is an undocumented precondition rather than a fixture.
 *
 * Covers: 422 for unregistered addresses (POST and PATCH — PATCH must not be
 * the bypass), 201 for registered roles, aliases, telemetry, Class V value tags
 * (including uppercase) and case variants of registered addresses, named
 * offender in the error, and no phantom row on rejection.
 *
 * SELF-SEEDED: unique titles per run, swept on success; a sweep-first pass
 * clears aborted-run residue so a prior failure cannot fail this run.
 *
 * Usage: npx tsx tests/tag-address-policy-http.test.ts
 *   POLICY_PHASE=warn|reject     run a single phase (default: both)
 *   NEBULA_TEST_BASE=...         override the phase-W base
 *   POLICY_REJECT_PORT=...       port for the spawned reject server (3199)
 */

import * as http from 'http';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';

const RUN = `tap-${Date.now()}`;
const LIVE_BASE = process.env.NEBULA_TEST_BASE || 'http://localhost:3101';
const REJECT_PORT = Number(process.env.POLICY_REJECT_PORT || 3199);
const POLICY_ENV = process.env.TAG_ADDRESS_POLICY_ENV_NAME || 'NEBULA_TAG_ADDRESS_POLICY';

function httpReq(
  base: string,
  method: string,
  reqPath: string,
  body?: any
): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : undefined;
    const url = new URL(reqPath, base);
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
    // Include the base in the message: a bare `err.message` for ECONNREFUSED is
    // empty, which is what made the old failure unreadable.
    req.on('error', (err) => reject(new Error(`Request failed (${method} ${base}${reqPath}): ${err.message || (err as any).code || String(err)}`)));
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

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function waitHealthy(base: string, timeoutMs = 90_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr = 'no attempt made';
  while (Date.now() < deadline) {
    try {
      const r = await httpReq(base, 'GET', '/health');
      if (r.status === 200) {
        console.log(`  ok: ${base} is healthy`);
        return;
      }
      lastErr = `health returned ${r.status}`;
    } catch (e: any) {
      lastErr = e.message;
    }
    await sleep(1000);
  }
  throw new Error(`server at ${base} never became healthy within ${timeoutMs}ms (last: ${lastErr})`);
}

async function spawnRejectServer(): Promise<{ base: string; child: ChildProcess }> {
  // The child must reach the SAME database as the live server. It inherits the
  // caller's PG_* env for that reason; CI exports it on the test step. Without
  // it the child falls back to the default `nexus` database, which the gate
  // refuses (pending migrations + NEBULA_MIGRATE_TARGET mismatch) — fail-closed
  // is the right behaviour, so a local run without a commissioned target stops
  // instead of migrating a live database.
  const base = `http://127.0.0.1:${REJECT_PORT}`;
  console.log(`starting reject-mode server on ${base}`);
  const child = spawn('npx', ['tsx', 'src/index.ts'], {
    cwd: path.resolve(__dirname, '..'),
    env: {
      ...process.env,
      PORT: String(REJECT_PORT),
      BIND_HOST: '127.0.0.1',
      [POLICY_ENV]: 'reject',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout?.on('data', (d) => process.stdout.write(`[reject-srv] ${d}`));
  child.stderr?.on('data', (d) => process.stderr.write(`[reject-srv] ${d}`));

  let earlyExit: number | null = null;
  child.on('exit', (code) => { if (earlyExit === null) earlyExit = code ?? -1; });

  try {
    await waitHealthy(base);
  } catch (e) {
    await stopServer(child);
    throw new Error(
      `${(e as Error).message}${earlyExit !== null ? ` (server exited early: code ${earlyExit})` : ''}`
    );
  }
  if (earlyExit !== null) {
    await stopServer(child);
    throw new Error(`reject-mode server exited (code ${earlyExit}) before serving requests`);
  }
  return { base, child };
}

async function stopServer(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      resolve();
    }, 10_000);
    child.once('exit', () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill('SIGTERM');
  });
}

async function sweep(base: string): Promise<void> {
  const probe = await httpReq(base, 'GET', `/api/agent-records?search=${encodeURIComponent(RUN)}&pageSize=500&includeContent=false`);
  for (const row of probe.body?.items ?? []) {
    await httpReq(base, 'DELETE', `/api/agent-records/${row.id}`);
  }
}

async function phaseWarn(base: string): Promise<void> {
  console.log(`phase W (warn) against ${base}`);
  await sweep(base);
  const created: string[] = [];
  try {
    const post = await httpReq(base, 'POST', '/api/agent-records', {
      recordType: 'engineering_log', role: 'dba',
      title: `${RUN} warn-phase unregistered`,
      content: `address-policy warn-phase fixture ${RUN} — safe to delete`,
      tags: ['to:definitely-not-registered-anywhere'],
    });
    ok('warn mode: unregistered to: address is ACCEPTED (201)', post.status === 201, post.status);
    created.push(post.body.id);

    const reread = await httpReq(base, 'GET', `/api/agent-records?id=${post.body.id}`);
    ok('warn mode: record persisted verbatim', (reread.body?.items ?? []).length === 1);
  } finally {
    for (const id of created) await httpReq(base, 'DELETE', `/api/agent-records/${id}`);
  }
}

async function phaseReject(base: string): Promise<void> {
  console.log(`phase R (reject) against ${base}`);
  await sweep(base);
  const created: string[] = [];
  try {
    // 1. unregistered Class R address => 422, not persisted
    const rej = await httpReq(base, 'POST', '/api/agent-records', {
      recordType: 'engineering_log', role: 'dba',
      title: `${RUN} reject-phase unregistered`,
      content: `must not persist ${RUN}`,
      tags: ['to:ghost-address'],
    });
    ok('reject mode: unregistered to:ghost-address => 422', rej.status === 422, { status: rej.status, body: rej.body });
    ok('reject mode: 422 names the offender', JSON.stringify(rej.body).includes('to:ghost-address'));

    const probe = await httpReq(base, 'GET', `/api/agent-records?search=${encodeURIComponent(RUN)}&pageSize=500&includeContent=false`);
    ok('reject mode: rejected record NOT persisted (no phantom row)', (probe.body?.items ?? []).length === 0, probe.body?.items?.length);

    // 2. registered role, alias, telemetry, Class V, case variants => 201
    const acceptSets: Array<[string, string[]]> = [
      ['registered role', ['to:dba']],
      ['registered alias to:all (kind=alias, never rejects)', ['to:all']],
      ['registered telemetry to:wr-conf-observer (kind=telemetry, never rejects)', ['to:wr-conf-observer']],
      ['Class V value tags incl. uppercase (Ruling 11: never reject)', ['type:change', 'ADR-006', 'blocks:PR-580']],
      ['mixed payload: only the unregistered member is flagged', ['to:dba', 'to:all', 'type:change', 'to:ghost-b']],
    ];
    for (const [label, tags] of acceptSets) {
      const isMixed = label.startsWith('mixed');
      const post = await httpReq(base, 'POST', '/api/agent-records', {
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

    // 2b. A case variant of a registered address never reaches the policy.
    // Main's routing guard (Rulings 38I/38J) rejects non-canonical routing tags
    // with 400 first, because `to:DBA` addresses a dead mailbox — inbox lookup
    // matches `to:`+role.toLowerCase() exactly. The policy itself is
    // case-insensitive by design and would accept it (covered in the unit
    // tier); the two guards are consistent, just scoped differently: the
    // routing guard owns deliverability, the policy owns registry membership.
    //
    // This assertion expected 201 until 2026-11-06, when it was written against
    // the branch's pre-38I premise and failed on rebase. The expectation moved;
    // the policy did not.
    const caseVariant = await httpReq(base, 'POST', '/api/agent-records', {
      recordType: 'engineering_log', role: 'dba',
      title: `${RUN} case variant`,
      content: `address-policy case fixture ${RUN} — safe to delete`,
      tags: ['to:DBA'],
    });
    ok('routing guard rejects uppercase to:DBA before the policy runs => 400', caseVariant.status === 400, { status: caseVariant.status, body: caseVariant.body });
    ok('400 names the canonical-lowercase requirement', JSON.stringify(caseVariant.body).toLowerCase().includes('lowercase'), caseVariant.body);

    // 3. PATCH enforcement: flipping tags onto an existing record is policed
    const seed = await httpReq(base, 'POST', '/api/agent-records', {
      recordType: 'engineering_log', role: 'dba',
      title: `${RUN} patch seed`,
      content: `address-policy patch fixture ${RUN} — safe to delete`,
      tags: ['to:dba'],
    });
    created.push(seed.body.id);
    const patchRej = await httpReq(base, 'PATCH', `/api/agent-records/${seed.body.id}`, {
      tags: ['to:dba', 'to:ghost-c'],
    });
    ok('reject mode: PATCH introducing unregistered address => 422', patchRej.status === 422, { status: patchRej.status, body: patchRej.body });
    const patchOk = await httpReq(base, 'PATCH', `/api/agent-records/${seed.body.id}`, {
      tags: ['to:dba', 'to:all'],
    });
    ok('reject mode: PATCH with registered addresses => 200', patchOk.status === 200, { status: patchOk.status, body: patchOk.body });

    console.log('PASS: tag address policy two-phase behavior verified');
  } finally {
    for (const id of created) await httpReq(base, 'DELETE', `/api/agent-records/${id}`);
    await sweep(base);
  }
}

async function main(): Promise<void> {
  const which = process.env.POLICY_PHASE || 'both';
  if (which === 'warn' || which === 'both') await phaseWarn(LIVE_BASE);
  if (which === 'reject' || which === 'both') {
    const { base, child } = await spawnRejectServer();
    try {
      await phaseReject(base);
    } finally {
      await stopServer(child);
      console.log('reject-mode server stopped');
    }
  }
}

main().catch((e: Error) => {
  console.error(e.message);
  process.exit(1);
});
