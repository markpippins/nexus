function equal(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
}
import { PgDoctrineLookup, type PgQueryable } from './doctrineLookup.pg.js';

/** Deterministic ISO strings for bitemporal tests. */
const T0 = '2026-01-01T00:00:00.000Z';
const T1 = '2026-06-01T00:00:00.000Z';
const AS_OF = '2026-08-28T00:00:00.000Z';

function row(over: Partial<Record<string, unknown>> = {}): Record<string, unknown> {
  return {
    id: 'doc-1',
    version: 1,
    digest: 'sha256:' + 'a'.repeat(64),
    effective_from: T0,
    superseded_at: null,
    source_decision_id: 'decision-1',
    kind: 'doctrine',
    ...over,
  };
}

/** Mock queryable returning a fixed row set (or throwing). */
function mockQueryable(rows: any[] | Error): PgQueryable & { calls: number } {
  const state = { calls: 0 };
  return {
    get calls() { return state.calls; },
    async query(_sql: string, _params?: unknown[]) {
      state.calls++;
      if (rows instanceof Error) throw rows;
      return { rows, rowCount: (rows as any[]).length };
    },
  } as PgQueryable & { calls: number };
}

export async function runPgDoctrineLookupConformance(): Promise<void> {
  // ── resolved: active row returned with provenance ──
  {
    const q = mockQueryable([row()]);
    const lookup = new PgDoctrineLookup({ queryable: q });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    equal(res.status, 'resolved', 'resolved status');
    equal(res.consulted, true, 'consulted flag');
    if (!res.record) throw new Error('resolved must carry record');
    equal(res.record.sourceDecisionId, 'decision-1', 'provenance: sourceDecisionId');
    equal(res.record.id, 'doc-1', 'record id');
    equal(res.record.digest.startsWith('sha256:'), true, 'digest prefixed');
    if (!(res.latencyMs >= 0)) throw new Error('latencyMs must be recorded');
    equal(q.calls, 1, 'exactly one query');
  }

  // ── unknown: stable id not in store ──
  {
    const lookup = new PgDoctrineLookup({ queryable: mockQueryable([]) });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'missing', asOf: AS_OF });
    equal(res.status, 'unknown', 'unknown lookup (not found)');
    equal(res.reason, 'stable_id_not_found', 'unknown reason');
  }

  // ── stale: only superseded/older rows effective at asOf ──
  {
    const lookup = new PgDoctrineLookup({
      queryable: mockQueryable([row({ effective_from: '2025-01-01T00:00:00.000Z', superseded_at: T1 })]),
    });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-old', asOf: AS_OF });
    equal(res.status, 'stale', 'stale lookup');
    equal(res.reason, 'stable_id_not_effective_at_as_of', 'stale reason');
  }

  // ── refusal: empty stable id / missing asOf ──
  {
    const lookup = new PgDoctrineLookup({ queryable: mockQueryable([]) });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: '', asOf: AS_OF });
    equal(res.status, 'refusal', 'refused lookup (empty id)');
    equal(res.consulted, false, 'refusal not consulted');
    const res2 = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: '' });
    equal(res2.status, 'refusal', 'refused lookup (empty asOf)');
  }

  // ── effective-date determinism: two effective rows → latest effective_from wins; version tiebreak ──
  {
    const lookup = new PgDoctrineLookup({
      queryable: mockQueryable([row({ version: 1, effective_from: T0 }), row({ version: 2, effective_from: T1, source_decision_id: 'decision-2' })]),
    });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    equal(res.status, 'resolved', 'multi-version resolved');
    equal(res.record!.version, 2, 'latest effective_from wins');
    equal(res.record!.sourceDecisionId, 'decision-2', 'provenance follows selected row');
  }

  // ── supersession-by-insertion: newer row supersedes older at asOf ──
  {
    const lookup = new PgDoctrineLookup({
      queryable: mockQueryable([
        row({ version: 1, effective_from: T0, superseded_at: T1 }),
        row({ version: 2, effective_from: T1, superseded_at: null }),
      ]),
    });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    equal(res.record!.version, 2, 'superseding row selected');
    // as-of before supersession → old row still active
    const res2 = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: '2026-03-01T00:00:00.000Z' });
    equal(res2.record!.version, 1, 'bitemporal as-of selects historical row');
  }

  // ── FAIL-CLOSED: query error → unknown/lookup_error, never throws ──
  {
    const lookup = new PgDoctrineLookup({ queryable: mockQueryable(new Error('connection refused')) });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    equal(res.status, 'unknown', 'fail-closed on query error');
    equal(res.reason, 'lookup_error', 'error reason taxonomy');
    equal(res.consulted, true, 'consulted before failure');
  }

  // ── FAIL-CLOSED: timeout → unknown/lookup_timeout ──
  {
    const slow: PgQueryable = {
      async query() {
        await new Promise((r) => setTimeout(r, 200));
        return { rows: [row()] };
      },
    };
    const lookup = new PgDoctrineLookup({ queryable: slow, timeoutMs: 30 });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    equal(res.status, 'unknown', 'fail-closed on timeout');
    equal(res.reason, 'lookup_timeout', 'timeout reason taxonomy');
  }

  // ── FAIL-CLOSED: malformed rows skipped; all-malformed → unknown ──
  {
    const lookup = new PgDoctrineLookup({
      queryable: mockQueryable([{ bad: 'shape' }, { id: null, version: 'x' }]),
    });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    equal(res.status, 'unknown', 'all-malformed rows → unknown');
  }

  // ── malformed row + valid row: valid one still resolves (row-level skip) ──
  {
    const lookup = new PgDoctrineLookup({
      queryable: mockQueryable([{ bad: 'shape' }, row({ version: 3, source_decision_id: 'decision-3' })]),
    });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    equal(res.status, 'resolved', 'valid row resolves among malformed');
    equal(res.record!.version, 3, 'valid row payload intact');
  }

  // ── assertBlockingLookup refuses non-resolved (fail-closed end-to-end) ──
  {
    const { assertBlockingLookup } = await import('./doctrineLookup.js');
    const lookup = new PgDoctrineLookup({ queryable: mockQueryable(new Error('db down')) });
    const res = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    let threw = false;
    try {
      assertBlockingLookup(res);
    } catch {
      threw = true;
    }
    equal(threw, true, 'assertBlockingLookup refuses unknown (fail-closed)');
  }

  // ── determinism: same input twice → identical result ──
  {
    const lookup = new PgDoctrineLookup({ queryable: mockQueryable([row()]) });
    const a = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    const b = await lookup.lookup({ kind: 'doctrine', stableId: 'doc-1', asOf: AS_OF });
    equal(JSON.stringify({ s: a.status, r: a.record }), JSON.stringify({ s: b.status, r: b.record }), 'deterministic repeat lookup');
  }

  console.log('pg doctrine lookup: conformance passed');
}

// Self-run when executed directly via tsx.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const proc: any = (globalThis as { process?: any }).process;
const isMain = proc?.argv?.[1] && import.meta.url.endsWith(proc.argv[1].split('/').pop() ?? '@@');
if (isMain) {
  runPgDoctrineLookupConformance().then(
    () => {
      proc.exit(0);
    },
    (e: unknown) => {
      console.error(e);
      proc.exit(1);
    },
  );
}
