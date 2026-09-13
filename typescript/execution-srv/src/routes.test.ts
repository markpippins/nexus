/**
 * W2.06 witnessed-runs conformance test — execution-srv.
 *
 * Dependency-free, `tsx`-driven (same convention as the §10 core
 * `scripts/run-*-conformance.ts`). Drives the exported `witnessedRunHandler`
 * with a mocked pg `Pool` + a minimal Express req/res shim, covering:
 *
 *   AC1  admission join — receipt + evidence populated, HTTP 200
 *   AC2  explicit state classification — all 7-state vocabulary cases
 *   AC3  PEB admission and Conduit transition receipt references stay
 *        separate (distinct ids; no fabricated/shared receipt identity)
 *   AC5  partial / missing lineage, drift, refusal, duplicate-retry
 *   v3   ruling ffa4ffc5 — phantom metadata legs gone; envelope/manifest/
 *        law/replay render null; assessment/evidence from resolution.*;
 *        business_key identity matching
 *
 * Run:  tsx src/routes.test.ts   (from execution-srv)
 */
import { Pool } from 'pg';
import {
  witnessedRunHandler,
  witnessedRunDiagnosticsHandler,
  witnessedRunProjectionHandler,
  buildAssessmentFromRow,
  buildEvidenceFromRow,
  classifyWitnessedRunStatus,
  WITNESSED_RUN_PROJECTION_VERSION,
} from './routes.js';
import type { WitnessedRunStatus } from './routes.js';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error('W2.06 conformance FAIL: ' + msg);
}

/** Minimal pg.Pool stand-in returning a fixed row set for every query. */
class MockPool {
  constructor(private readonly rows: unknown[]) {}
  get query() {
    return async () => ({ rows: this.rows, rowCount: this.rows.length });
  }
}

interface CallResult {
  status: number;
  body: any;
}

/** Invoke the handler with a mock req/res and capture status + body. */
function callHandler(
  handler: (req: any, res: any) => Promise<any>,
  query: Record<string, string>,
): Promise<CallResult> {
  return new Promise((resolve) => {
    const res = {
      _status: 200,
      status(code: number) {
        res._status = code;
        return res;
      },
      set(_k: string, _v: string) { return res; },
      // `badRequest`/`notFound` call res.status(...).json(...); resolve on json.
      json: (payload: unknown) => resolve({ status: res._status, body: payload }),
      send: (payload: unknown) => resolve({ status: res._status, body: payload }),
    } as any;
    const req: any = { query, params: {} };
    return handler(req, res);
  });
}

/**
 * v3 row shape (ruling ffa4ffc5): flat receipt/evidence columns from the
 * admission join; envelope/manifest/law/replay are NULL at the source.
 */
function v3Row(over: Partial<Record<string, unknown>> = {}): Record<string, unknown> {
  return {
    request_id: '11111111-1111-4111-8111-111111111111',
    workflow_instance_id: 'wf-0007',
    envelope: null,
    manifest: null,
    law: null,
    assessment_admitted: true,
    assessment_reason: 'verified Git evidence is eligible for PEB admission',
    assessment_source_system: 'git-verifier',
    assessment_policy_hash: 'sha256:policy',
    evidence_id: '4a4ea9ba-e3bd-4d88-b1c8-205ec36bdc13',
    evidence_fingerprint: 'sha256:evidence',
    evidence_payload: { outcome: 'verified', reason: 'ok', claimed_ref: 'refs/heads/main' },
    updated_at: new Date(0).toISOString(),
    peb_admission: '77777777-3333-4444-8555-666666666666',
    conduit_transition: '88888888-4444-4555-8666-777777777777',
    ...over,
  };
}

const QUERY = { workflow_instance_id: 'wf-0007', node_id: 'node-admission' };

export async function runWitnessedRunRoutesConformance(): Promise<void> {
  // ── AC1 admission join (route-level) ──────────────────────────────────────
  {
    const handler = witnessedRunHandler(new MockPool([v3Row()]) as unknown as Pool);
    const { status, body } = await callHandler(handler, QUERY);
    assert(status === 200, 'admission join should be HTTP 200');
    const p = body.projection;
    // v3: receipt + assessment + evidence populated from resolution.*.
    assert(p.receipts.pebAdmission === '77777777-3333-4444-8555-666666666666', 'peb admission projected');
    assert(p.receipts.conduitTransition === '88888888-4444-4555-8666-777777777777', 'conduit transition projected');
    assert(p.assessment.disposition === true, 'assessment.disposition is ar.admitted (boolean)');
    assert(p.assessment.status === 'admitted', 'assessment.status derives from admitted=true');
    assert(p.assessment.reason === 'verified Git evidence is eligible for PEB admission', 'assessment.reason from ar.reason');
    assert(p.assessment.sourceSystem === 'git-verifier', 'assessment.sourceSystem from ar.source_system');
    assert(p.assessment.policyVersionHash === 'sha256:policy', 'assessment.policyVersionHash from ar.policy_version_hash');
    assert(Array.isArray(p.evidence.ids) && p.evidence.ids[0] === '4a4ea9ba-e3bd-4d88-b1c8-205ec36bdc13', 'evidence.ids from ev.id');
    assert(p.evidence.fingerprint === 'sha256:evidence', 'evidence.fingerprint from ev.source_hash');
    assert(p.evidence.payload.outcome === 'verified', 'evidence.payload from ev.payload (provenance surface)');
    // v3: phantom fields render null.
    assert(p.envelope.id === null && p.envelope.evaluationFingerprint === null, 'envelope renders null');
    assert(p.manifest.id === null, 'manifest renders null');
    assert(Array.isArray(p.law.propositionIds) && p.law.propositionIds.length === 0, 'law renders null-shaped');
    assert(p.replay.fixtureId === null && p.replay.status === null, 'replay renders null');
    assert(p.workflow.instanceId === 'wf-0007', 'workflow identity preserved (AC4 no browser reconstruction)');
    // Status is missing_lineage: envelope/manifest have no producer backing yet.
    assert(p.status === 'missing_lineage', 'v3 status is missing_lineage (envelope/manifest not yet produced)');
  }

  // ── AC2 classification matrix (unit) — classifier is unchanged/verbatim ──
  const classify = (over: {
    row?: Record<string, unknown>;
    envelope?: Record<string, unknown>;
    manifest?: Record<string, unknown>;
    assessment?: Record<string, unknown>;
    replay?: Record<string, unknown>;
  }): WitnessedRunStatus => {
    const base = { ...v3Row(), ...(over.row ?? {}) };
    // v3 rows carry NULLs at the source; handlers pass `{}` for the null
    // fields and inject the built evidence into the row — the helper mirrors
    // that exactly before invoking the classifier.
    const envelope = over.envelope ?? {};
    const manifest = over.manifest ?? {};
    const assessment = over.assessment ?? buildAssessmentFromRow(base);
    const replay = over.replay ?? {};
    const row = { ...base, evidence: buildEvidenceFromRow(base) } as Record<string, unknown>;
    return classifyWitnessedRunStatus({ envelope, manifest, assessment, replay, row });
  };
  const produced = new Set<WitnessedRunStatus>();
  produced.add(classify({ envelope: { id: 'env-1', evaluationFingerprint: 'fp' }, manifest: { id: 'm-1' } })); // complete
  produced.add(classify({})); // missing_lineage (v3 baseline: nulls + receipts present)
  produced.add(classify({ envelope: { id: 'env-1', evaluationFingerprint: 'fp' }, manifest: { id: 'm-1' }, row: { peb_admission: null } }));
  produced.add(classify({ replay: { fixtureId: 'F01', status: 'stale' } }));
  produced.add(classify({ assessment: { disposition: 'refuse', status: 'refused' } }));
  produced.add(classify({ replay: { fixtureId: 'F01', status: 'drift' } }));
  produced.add(classify({ replay: { fixtureId: 'F01', status: 'duplicate_retry' } }));

  assert(produced.has('complete'), 'AC2 complete state produced (classifier unchanged)');
  assert(produced.has('missing_lineage'), 'AC2 missing_lineage state produced');
  assert(produced.has('stale'), 'AC2 stale state produced');
  assert(produced.has('refusal'), 'AC2 refusal state produced');
  assert(produced.has('drift'), 'AC2 drift state produced');
  assert(produced.has('duplicate_retry'), 'AC2 duplicate_retry state produced');

  // ── AC3 PEB / Conduit receipt-reference separation ───────────────────────
  const missingConduit = { peb_admission: '77777777-3333-4444-8555-666666666666', conduit_transition: null };
  assert(
    classify({ envelope: { id: 'env-1', evaluationFingerprint: 'fp' }, manifest: { id: 'm-1' }, row: missingConduit }) === 'missing_lineage',
    'AC3 missing conduit transition -> missing_lineage (PEB reference preserved, no fabricated Conduit id)',
  );

  const payload = callHandler(
    witnessedRunHandler(new MockPool([v3Row()]) as unknown as Pool),
    QUERY,
  );
  const p = (await payload).body.projection;
  const allDistinct = new Set([p.receipts.pebAdmission, p.receipts.conduitTransition]);
  assert(allDistinct.size === 2, 'AC3 PEB and Conduit receipt ids remain distinct');

  // ── AC5 partial / missing lineage + edge states ──────────────────────────
  // PR #70 review finding: a row with NO lineage at all must classify as
  // 'unknown' (indeterminate), not 'missing_lineage' (partial witnessed run).
  assert(
    classify({
      row: {
        assessment_admitted: null, evidence_id: null,
        peb_admission: null, conduit_transition: null,
      },
    }) === 'unknown',
    'AC5 empty lineage -> unknown (indeterminate, not a partial witnessed run)',
  );
  assert(classify({}) === 'missing_lineage', 'AC5 partial lineage (receipts only) -> missing_lineage');
  assert(
    classify({ row: { peb_admission: null, conduit_transition: null, assessment_admitted: null, evidence_id: null } }) === 'unknown',
    'AC5 receipts-only-null row -> unknown',
  );
  assert(classify({ replay: { fixtureId: 'F01', status: 'stale' } }) === 'stale', 'AC5 stale classified');
  assert(classify({ assessment: { disposition: 'refuse', status: 'refused' } }) === 'refusal', 'AC5 refusal classified');
  assert(classify({ replay: { fixtureId: 'F01', status: 'drift' } }) === 'drift', 'AC5 drift classified');
  assert(classify({ replay: { fixtureId: 'F01', status: 'duplicate_retry' } }) === 'duplicate_retry', 'AC5 duplicate retry classified');

  // not-found path
  const nf = await callHandler(
    witnessedRunHandler(new MockPool([]) as unknown as Pool),
    QUERY,
  );
  assert(nf.status === 404, 'no matching row -> 404');

  // ── v3 rejected receipt (admitted=false) maps, never to 'refused' ────────
  {
    const rejected = buildAssessmentFromRow(v3Row({ assessment_admitted: false, assessment_reason: 'EVIDENCE_NOT_INDEPENDENTLY_VERIFIED' }));
    assert(rejected.disposition === false, 'rejected receipt -> disposition=false');
    assert(rejected.status === 'rejected', 'rejected receipt -> status=rejected (refusal reserved for governance)');
  }
  // No receipt at all → all-null assessment.
  {
    const none = buildAssessmentFromRow(v3Row({ assessment_admitted: null }));
    assert(none.disposition === null && none.status === null && none.reason === null, 'no receipt -> all-null assessment');
  }

  // ── diagnostics route (W3.05) ─────────────────────────────────────────────
  {
    const handler = witnessedRunDiagnosticsHandler(new MockPool([v3Row()]) as unknown as Pool);
    const { status, body } = await callHandler(handler, QUERY);
    assert(status === 200, 'diagnostics 200 on v3 row');
    assert(body.projection.status === 'missing_lineage', 'diagnostics server-derived status');
    const missing = body.projection.missing_lineage_elements as string[];
    assert(missing.includes('envelope'), 'diagnostics enumerates missing envelope');
    assert(missing.includes('manifest'), 'diagnostics enumerates missing manifest');
    assert(!missing.includes('peb_admission_receipt'), 'present peb receipt not listed missing');
    assert(!missing.includes('evidence'), 'present evidence not listed missing');
    assert(body.projection.receipt_correlation.pebAdmission.valid === true, 'receipt correlation validated');
    assert(body.projection.assessment.disposition === true, 'diagnostics assessment disposition from receipt');
  }

  // ── W3.08 governed projection endpoint ───────────────────────────────
  {
    const handler = witnessedRunProjectionHandler(
      new MockPool([v3Row()]) as unknown as Pool,
    );
    const { status, body } = await callHandler(handler, QUERY);
    assert(status === 200, 'projection complete join -> 200');
    assert(body.projectionVersion === WITNESSED_RUN_PROJECTION_VERSION,
      'projection carries its version');
    assert(WITNESSED_RUN_PROJECTION_VERSION === 3, 'projection version is v3 (ruling ffa4ffc5)');
    assert(body.projection === 'witnessed-run', 'projection type labeled');
    assert(typeof body.generatedAt === 'string', 'generatedAt present');
    assert(body.status === 'missing_lineage', 'server-derived authoritative status');
    const ml = body.missingLineage as string[];
    assert(ml.includes('envelope_id') && ml.includes('evaluation_fingerprint') && ml.includes('manifest_id'),
      'missing envelope/manifest identities enumerated');
    assert(!ml.includes('peb_admission_receipt') && !ml.includes('evidence_ids'),
      'present identities not listed as missing');
    assert(body.identities.pebAdmissionReceiptId === '77777777-3333-4444-8555-666666666666',
      'peb receipt id correlated');
    assert(body.identities.conduitTransitionReceiptId === '88888888-4444-4555-8666-777777777777',
      'conduit receipt id correlated (separate identity, AC3)');
    assert(Array.isArray(body.identities.evidenceIds) && body.identities.evidenceIds[0] === '4a4ea9ba-e3bd-4d88-b1c8-205ec36bdc13',
      'evidence identity correlated from resolution.*');
    assert(body.request.id === '11111111-1111-4111-8111-111111111111', 'request id correlated');
    assert(body.assessment.disposition === true, 'projection assessment disposition');
    // No governance payloads leak — only identity correlation + derived status.
    const flat = JSON.stringify(body);
    assert(!flat.includes('payload'), 'no evidence payload in governed projection');
    assert(!flat.includes('"law"'), 'no law payload in projection');

    // Partial lineage: missing elements enumerated, status server-derived.
    const partial = { ...v3Row(), peb_admission: null, conduit_transition: null };
    const pr = await callHandler(
      witnessedRunProjectionHandler(new MockPool([partial]) as unknown as Pool),
      QUERY,
    );
    assert(pr.status === 200, 'partial join still 200');
    assert(pr.body.status === 'missing_lineage', 'partial join classified server-side');
    const pml = pr.body.missingLineage as string[];
    assert(pml.includes('peb_admission_receipt'), 'missing peb receipt enumerated');
    assert(pml.includes('conduit_transition_receipt'), 'missing conduit receipt enumerated');

    // Empty row (no lineage at all) -> unknown + full enumeration.
    const empty = v3Row({
      envelope: null, manifest: null, assessment_admitted: null, evidence_id: null,
      updated_at: null, peb_admission: null, conduit_transition: null,
    });
    const ur = await callHandler(
      witnessedRunProjectionHandler(new MockPool([empty]) as unknown as Pool),
      QUERY,
    );
    assert(ur.status === 200, 'unknown row still 200');
    assert(ur.body.status === 'unknown', 'empty lineage -> unknown');
    assert((ur.body.missingLineage as string[]).length === 6, 'all six lineage elements enumerated');

    // 404 path
    const nf = await callHandler(
      witnessedRunProjectionHandler(new MockPool([]) as unknown as Pool),
      QUERY,
    );
    assert(nf.status === 404, 'projection 404 on unknown run');

    // 400 path
    const br = await callHandler(
      witnessedRunProjectionHandler(new MockPool([v3Row()]) as unknown as Pool),
      { workflow_instance_id: 'wf-0007' },
    );
    assert(br.status === 400, 'projection 400 without node_id');
  }

  console.log('witnessed-run routes: conformance passed');
}

// Self-run when executed directly via tsx.
if (typeof require !== 'undefined' && require.main === module) {
  runWitnessedRunRoutesConformance().then(
    () => {
      console.log('ALL GREEN');
      process.exit(0);
    },
    (err) => {
      console.error(err?.message ?? err);
      process.exit(2);
    },
  );
}
