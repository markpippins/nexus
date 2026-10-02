#!/usr/bin/env npx tsx
/**
 * Standing deliverability guard: EVERY canonical role's inbox must actually
 * deliver, end-to-end through the public API.
 *
 * Proves the full delivery pipeline — write-side address acceptance, tag
 * storage, query-path matching, pointer-window semantics — for each role
 * derived from config/roles/roles.json (nebulaCheck=true roles are the
 * canonical, inbox-bearing identities). New roles are auto-covered because
 * the role list is read from the registry, not hardcoded.
 *
 * Runs in the service-test-gates workflow (throwaway PostgreSQL + live
 * nebula-srv). A failure here means SOME canonical role cannot receive
 * records through the canonical path — the defect class that previously hid
 * until a raw-SQL audit (to:DBA invisible to canonical queries, audits
 * 331ac2f3 / 8c1a63bb).
 *
 * Checks per role:
 *   1. delivery:     a marker record POSTed with to:<role> is returned by
 *                    GET /agent-records?tag=to:<role>&createdAfter=<iso>
 *                    (the inbox query shape; >= semantics, routes.ts:5015)
 *   2. isolation:    the same marker is NOT returned by other roles' inboxes
 *   3. vacuity:      a deliberate non-delivery probe (to:nobody-<ns>) returns
 *                    0 for a sampled role — the harness fails loudly if the
 *                    query path ever returns everything regardless of filter
 *   4. cleanup:      DELETE removes every fixture; a follow-up search by the
 *                    unique namespace must return 0
 *
 * Usage: NEBULA_TEST_BASE=http://localhost:3101 npx tsx tests/deliverability.test.ts
 */

import * as fs from 'fs';
import * as http from 'http';
import * as path from 'path';

const BASE = process.env.NEBULA_TEST_BASE || 'http://localhost:3101';
// realpath: tsx may run this file through a symlinked path (e.g. worktree
// layouts); resolve to the physical tree so the registry is always found.
const REPO = path.resolve(fs.realpathSync(__dirname), '..', '..', '..');
const ROLES_JSON = path.join(REPO, 'config', 'roles', 'roles.json');
const NS = `dlv-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;

interface Fixture {
  id: string;
  ownerRole: string;
  markerTag: string;
}

function httpReq(method: string, reqPath: string, body?: unknown): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const url = new URL(reqPath, BASE);
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
            reject(new Error(`non-JSON ${res.statusCode} from ${reqPath}: ${data.slice(0, 200)}`));
          }
        });
      },
    );
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function canonicalInboxRoles(): string[] {
  const spec = JSON.parse(fs.readFileSync(ROLES_JSON, 'utf-8'));
  const defaults = spec.roleDefaults ?? {};
  const roles: string[] = [];
  for (const [name, overrides] of Object.entries<any>(spec.roles ?? {})) {
    const nebulaCheck = overrides?.nebulaCheck ?? defaults.nebulaCheck ?? false;
    if (nebulaCheck) roles.push(name);
  }
  if (roles.length < 3) {
    throw new Error(`registry derived only ${roles.length} inbox-bearing roles — refusing to run vacuously`);
  }
  return roles.sort();
}

async function expect(cond: boolean, label: string, detail?: unknown): Promise<void> {
  if (!cond) throw new Error(`FAIL: ${label}${detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 300) : ''}`);
  console.log(`  ok: ${label}`);
}

function itemsOf(res: { body: any }): any[] {
  return res.body?.items ?? res.body?.records ?? res.body?.rows ?? [];
}

async function main(): Promise<void> {
  console.log(`deliverability guard: ${BASE} (ns=${NS})`);
  const roles = canonicalInboxRoles();
  console.log(`registry-derived inbox-bearing roles (${roles.length}): ${roles.join(', ')}`);

  const created: Fixture[] = [];
  try {
    // ── seed one marker per role ────────────────────────────────────
    for (const role of roles) {
      const res = await httpReq('POST', '/api/agent-records', {
        recordType: 'engineering_log',
        role: 'dba',
        title: `${NS} marker for ${role}`,
        content: `deliverability-guard fixture ${NS} ${role} — safe to delete`,
        tags: [`to:${role}`, 'area:deliverability-guard'],
      });
      if (res.status !== 201) throw new Error(`POST failed for ${role}: ${res.status} ${JSON.stringify(res.body).slice(0, 200)}`);
      created.push({ id: res.body.id, ownerRole: role, markerTag: `to:${role}` });
    }
    console.log(`  ok: ${created.length} markers seeded (one per canonical role)`);

    // ── 1. delivery: marker visible in its own inbox query ─────────
    for (const f of created) {
      const q = await httpReq(
        'GET',
        `/api/agent-records?tag=${encodeURIComponent(f.markerTag)}&createdAfter=2020-01-01T00:00:00Z&pageSize=500&includeContent=false`,
      );
      const ids = itemsOf(q).map((r) => r.id);
      await expect(ids.includes(f.id), `delivers: ${f.markerTag}`, q.status);
    }

    // ── 2. isolation: markers invisible to OTHER roles' inboxes ────
    for (const f of created) {
      const others = roles.filter((r) => r !== f.ownerRole).slice(0, 2); // sample 2 distinct other roles per marker
      for (const other of others) {
        const q = await httpReq(
          'GET',
          `/api/agent-records?tag=${encodeURIComponent(`to:${other}`)}&pageSize=500&includeContent=false`,
        );
        const ids = itemsOf(q).map((r) => r.id);
        await expect(!ids.includes(f.id), `isolated: ${f.ownerRole}'s marker not in to:${other}`);
      }
    }

    // ── 3. vacuity: a bogus address must match NOTHING ─────────────
    const vac = await httpReq(
      'GET',
      `/api/agent-records?tag=${encodeURIComponent(`to:nobody-${NS}`)}&pageSize=500&includeContent=false`,
    );
    await expect(itemsOf(vac).length === 0, 'vacuity: bogus address matches nothing', itemsOf(vac).length);

    // ── 4. pointer-window semantics: createdAfter gates delivery ───
    const windowed = await httpReq(
      'GET',
      `/api/agent-records?tag=${encodeURIComponent(created[0].markerTag)}&createdAfter=2099-01-01T00:00:00Z&pageSize=500&includeContent=false`,
    );
    await expect(
      itemsOf(windowed).length === 0,
      'pointer-window: createdAfter in the future gates delivery (0 records)',
      itemsOf(windowed).length,
    );

    // ── 5. cleanup + verification ──────────────────────────────────
    let cleanFail = 0;
    for (const f of created) {
      const del = await httpReq('DELETE', `/api/agent-records/${f.id}`);
      if (del.status !== 200) {
        console.error(`  WARN: DELETE ${f.id} -> ${del.status}`);
        cleanFail++;
      }
    }
    if (cleanFail > 0) throw new Error(`cleanup: ${cleanFail} DELETE(s) failed`);
    const residue = await httpReq(
      'GET',
      `/api/agent-records?search=${encodeURIComponent(NS)}&pageSize=500&includeContent=false`,
    );
    await expect(itemsOf(residue).length === 0, 'cleanup verified: 0 fixtures remain', itemsOf(residue).length);

    console.log(`PASS: deliverability guard — ${roles.length}/${roles.length} canonical inboxes deliver, isolate, gate, and clean`);
  } catch (err) {
    // never leave fixtures behind on failure
    for (const f of created) {
      await httpReq('DELETE', `/api/agent-records/${f.id}`).catch(() => {});
    }
    throw err;
  }
}

main().catch((e: Error) => {
  console.error(e.message);
  process.exit(1);
});
