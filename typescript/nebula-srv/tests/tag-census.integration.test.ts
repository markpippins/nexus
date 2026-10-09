#!/usr/bin/env npx tsx
/**
 * Service-tier runner: §8 census guard over the live corpus (card `ef1b5077`
 * §8, Ruling 17 F2).
 *
 * Crawls `GET /api/agent-records` and reports every record whose stored tags
 * are not a fixed point of the write-side normalizer — i.e. every record that
 * could not have been written through the REST write path.
 *
 * ── Why REST and not SQL ───────────────────────────────────────────────────
 *
 * A census is strongest when it reads the same surface the delivery path
 * reads: an unauthorized direct-DB writer is detected through the same API
 * operators actually query, with no database credentials in the runner and no
 * second connection path that could itself diverge. `normalizeTags` is called
 * only on POST/PATCH, so reads return stored bytes VERBATIM — the census sees
 * the truth, not a normalized projection of it.
 *
 * ── Epoch, and the deliberate weak-CI default ──────────────────────────────
 *
 * `NEBULA_TAG_CENSUS_EPOCH` (ISO) is the instant the write-side normalizer
 * became effective. Only records at/after it are VIOLATIONS; earlier ones are
 * BASELINE. With no epoch set the runner reports a baseline and exits 0.
 *
 * That default is intentional and it is the honest one: the normalizer is not
 * deployed yet (#714 is an open draft), so every dirty record in the corpus is
 * historical and would otherwise be a false accusation. §4 is write-side only —
 * historical rows are never rewritten. Until the epoch is set, this runner
 * cannot enforce anything in CI, and says so on stdout rather than pretending
 * otherwise.
 *
 * What CI DOES prove today, on an empty database: the crawl works against real
 * API shapes, and — via the self-check below — that the detector actually
 * flags a known-dirty record rather than passing vacuously.
 *
 * Usage:
 *   NEBULA_TEST_BASE=http://localhost:3101 \
 *     npx tsx tests/tag-census.integration.test.ts
 *   NEBULA_TAG_CENSUS_EPOCH=2026-10-05T00:00:00Z   # arm enforcement
 */

import * as http from 'http';

import { runCensus, classifyRecord, summarizeCensus, type CensusRecord } from '../src/tagCensus';

const BASE = process.env.NEBULA_TEST_BASE || 'http://localhost:3101';
const EPOCH = process.env.NEBULA_TAG_CENSUS_EPOCH || null;
const PAGE_SIZE = 100; // the list endpoint caps pageSize at 100
const MAX_PAGES = parseInt(process.env.NEBULA_TAG_CENSUS_MAX_PAGES || '400', 10);
const SHOW = parseInt(process.env.NEBULA_TAG_CENSUS_SHOW || '20', 10);

function httpReq(path: string): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const url = new URL(path, BASE);
    const req = http.request(
      { hostname: url.hostname, port: url.port, path: url.pathname + url.search, method: 'GET' },
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
    req.end();
  });
}

let failures = 0;
function check(cond: boolean, label: string, detail?: unknown): void {
  if (!cond) {
    failures++;
    console.error(`  FAIL: ${label}${detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 400) : ''}`);
  } else {
    console.log(`  ok: ${label}`);
  }
}

/**
 * Crawl every record. Deduplicated by id: this is offset paging over a live
 * table, so a concurrent insert can shift a row across a page boundary and
 * yield it twice. Reporting a duplicate finding would be a false accusation.
 */
async function crawl(epochIso: string | null): Promise<CensusRecord[]> {
  const byId = new Map<string, CensusRecord>();
  let page = 1;
  let total = 0;

  for (; page <= MAX_PAGES; page++) {
    const q = new URLSearchParams({ page: String(page), pageSize: String(PAGE_SIZE), includeContent: 'false' });
    if (epochIso) q.set('createdAfter', epochIso);
    const res = await httpReq(`/api/agent-records?${q.toString()}`);
    if (res.status !== 200) throw new Error(`list page ${page} -> ${res.status}`);

    total = res.body.total ?? 0;
    const items: any[] = res.body.items ?? [];
    for (const it of items) byId.set(it.id, { id: it.id, createdAt: it.createdAt, tags: it.tags });
    if (items.length === 0) break;
    if (page === 1) console.log(`  corpus total=${total}`);
  }

  if (page > MAX_PAGES && total > 0) {
    console.error(`  WARN: stopped at MAX_PAGES=${MAX_PAGES} of total=${total} — census is INCOMPLETE`);
  }
  return [...byId.values()];
}

async function main(): Promise<void> {
  console.log(`§8 census guard against ${BASE} (epoch=${EPOCH ?? 'UNSET -> baseline only'})`);

  // ── Self-check FIRST: prove the detector is armed before trusting a pass ──
  // A census that cannot flag a known bypass is worse than no census, because
  // it reads as enforcement. On an empty CI database this is the only assertion
  // with teeth, so it must not be skipped.
  const canary = classifyRecord({ id: 'canary', createdAt: '2026-10-02T00:00:00.000Z', tags: ['  to:DBA  '] });
  check(canary !== null, 'self-check: detector flags an untrimmed uppercase Class R address');
  check(
    canary?.reasons.includes('class-r-not-normalized') ?? false,
    'self-check: reason is class-r-not-normalized',
    canary?.reasons,
  );
  const cleanCanary = classifyRecord({ id: 'canary-ok', createdAt: '2026-10-02T00:00:00.000Z', tags: ['to:dba', 'blocks:PR-580'] });
  check(cleanCanary === null, 'self-check: detector does NOT flag a correctly written record');

  const records = await crawl(EPOCH);
  console.log(`  crawled ${records.length} record(s)`);

  const report = runCensus(records, { epochIso: EPOCH });

  for (const f of report.findings.slice(0, SHOW)) {
    console.log(
      `  finding ${f.id} @${f.createdAtIso} [${f.reasons.join(',')}] ` +
        `stored=${JSON.stringify(f.offendingTags).slice(0, 160)} ` +
        `would-store=${JSON.stringify(f.normalizedForm).slice(0, 160)}`,
    );
  }
  if (report.findings.length > SHOW) {
    console.log(`  ... and ${report.findings.length - SHOW} more finding(s)`);
  }
  console.log(`  ${summarizeCensus(report)}`);

  if (report.baselineOnly) {
    console.log(
      '  NOTE: NEBULA_TAG_CENSUS_EPOCH is unset, so nothing is treated as a VIOLATION.\n' +
        '        The write-side normalizer is not deployed yet; all findings are historical\n' +
        '        baseline. Set the epoch to the instant §4 went live to arm enforcement.',
    );
  } else {
    console.log(`  epoch armed: ${report.violations.length} post-epoch violation(s)`);
    check(
      report.violations.length === 0,
      'no post-epoch record bypasses the write-side normalizer',
      report.violations.slice(0, 5).map((v) => ({ id: v.id, at: v.createdAtIso, reasons: v.reasons })),
    );
  }

  // A crawl that silently returned almost nothing must not read as "clean".
  check(records.length > 0 || report.scanned === 0, 'crawl returned records or an empty corpus', records.length);

  if (failures > 0) {
    console.error(`FAIL: ${failures} census assertion(s) failed against ${BASE}`);
    process.exit(1);
  }
  console.log('PASS: §8 census guard completed');
}

main().catch((e: Error) => {
  console.error(e.message);
  process.exit(1);
});