import "dotenv/config";
import { Errors, Service, ServiceBroker, Context } from "moleculer";
import { Pool } from "pg";

/**
 * worker.execution.witnessed-runs — provenance projection worker (M2).
 *
 * Ports execution-srv's witnessed-run family into broker actions — the four
 * routes the Wave-4.3 port (execution.worker.ts) did not cover:
 *
 *   GET /api/execution/witnessed-runs              -> witnessedRuns
 *   GET /api/execution/witnessed-runs/diagnostics  -> witnessedRunDiagnostics
 *   GET /api/execution/projections/witnessed-runs  -> witnessedRunProjection
 *   GET /api/execution/metrics                     -> governanceMetrics
 *
 * The classifier (`classifyWitnessedRunStatus`) is ported verbatim from
 * execution-srv routes.ts — it is the AUTHORITATIVE join-state derivation
 * (W3.05 AC4: no client-side reconstruction), so both surfaces must agree
 * to the byte. All queries are SELECT-only (read-only observability).
 *
 * Metrics note: the registry below is worker-local (per process). The legacy
 * service's registry lives in the execution-srv process; counters are
 * therefore not shared across surfaces — same as the search limiter's
 * fail-silent Redis note, this is recorded in the slice docs.
 */

// ── Metrics registry (verbatim port of execution-srv metrics.ts) ──────

class MetricsRegistry {
  private counters = new Map<string, number>();
  private latency = new Map<string, { sum: number; count: number }>();

  inc(name: string, labels: Record<string, string> = {}, by = 1): void {
    const key = this.key(name, labels);
    this.counters.set(key, (this.counters.get(key) ?? 0) + by);
  }

  observeLatency(name: string, labels: Record<string, string>, ms: number): void {
    const key = this.key(name, labels);
    const cur = this.latency.get(key) ?? { sum: 0, count: 0 };
    cur.sum += ms;
    cur.count += 1;
    this.latency.set(key, cur);
  }

  snapshot(): Record<string, unknown> {
    const counters: Record<string, number> = {};
    for (const [k, v] of this.counters) counters[k] = v;
    const latencies: Record<string, { sum: number; count: number; avg: number }> = {};
    for (const [k, v] of this.latency) latencies[k] = { ...v, avg: v.count === 0 ? 0 : Math.round((v.sum / v.count) * 1000) / 1000 };
    return { counters, latencies, generatedAt: new Date().toISOString() };
  }

  private key(name: string, labels: Record<string, string>): string {
    const parts = Object.keys(labels).sort().map((k) => `${k}=${String(labels[k]).replace(/[^\w.-]/g, "_")}`);
    return parts.length ? `${name}{${parts.join(",")}}` : name;
  }
}

const governanceMetrics = new MetricsRegistry();

const METRIC_WITNESSED_RUN_STATUS = "witnessed_run_status_total";
const METRIC_RECEIPT_CORRELATION_INVALID = "receipt_correlation_invalid_total";

/** Loose UUID shape check mirroring resolution.is_uuid(text) semantics. */
function isUuidShape(value: unknown): boolean {
  if (typeof value !== "string" || value.length === 0) return false;
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

// ── Witnessed-run status classifier (verbatim port) ───────────────────

type WitnessedRunStatus =
  | "complete"
  | "missing_lineage"
  | "unknown"
  | "stale"
  | "refusal"
  | "drift"
  | "duplicate_retry";

function classifyWitnessedRunStatus(input: {
  envelope: Record<string, unknown>;
  manifest: Record<string, unknown>;
  assessment: Record<string, unknown>;
  replay: Record<string, unknown>;
  row: Record<string, unknown>;
}): WitnessedRunStatus {
  const { envelope, manifest, assessment, replay, row } = input;
  if (replay.status === "stale" || assessment.status === "stale") return "stale";
  if (replay.status === "drift" || assessment.status === "drift") return "drift";
  if (replay.status === "duplicate_retry") return "duplicate_retry";
  if (assessment.status === "refused" || assessment.disposition === "refuse") return "refusal";

  const envelopeId = (envelope.id ?? envelope.envelope_id) as string | undefined;
  const fingerprint = envelope.evaluationFingerprint ?? envelope.evaluation_fingerprint;
  const manifestId = (manifest.id ?? manifest.artifact_id ?? manifest.artifactId) as string | undefined;
  const evidenceBlob = row.evidence as Record<string, unknown> | undefined;
  const evidenceIds = (evidenceBlob?.ids ?? evidenceBlob?.evidence_ids) as string[] | undefined;
  const hasEnvelope = Boolean(envelopeId && fingerprint);
  const hasManifest = Boolean(manifestId);
  const hasReceipts = Boolean(row.peb_admission && row.conduit_transition);
  const hasEvidence = Boolean(evidenceIds && evidenceIds.length > 0);

  if (!hasEnvelope && !hasManifest && !hasReceipts && !hasEvidence) return "unknown";
  if (!hasEnvelope || !hasManifest || !hasReceipts || !hasEvidence) return "missing_lineage";

  return "complete";
}

// W3.08 — bump only on breaking shape changes to the projection payload.
const WITNESSED_RUN_PROJECTION_VERSION = 1;

interface WitnessedRunParams {
  workflow_instance_id: string;
  node_id: string;
}

export default class WitnessedRunsWorker extends Service {
  private pool: Pool | null = null;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "worker.execution.witnessed-runs",

      actions: {
        witnessedRuns: {
          params: {
            workflow_instance_id: { type: "string", optional: true },
            node_id: { type: "string", optional: true },
          },
          handler: (ctx: Context<WitnessedRunParams>) => this.witnessedRuns(ctx.params),
        },

        witnessedRunDiagnostics: {
          params: {
            workflow_instance_id: { type: "string", optional: true },
            node_id: { type: "string", optional: true },
          },
          handler: (ctx: Context<WitnessedRunParams>) => this.witnessedRunDiagnostics(ctx.params),
        },

        witnessedRunProjection: {
          params: {
            workflow_instance_id: { type: "string", optional: true },
            node_id: { type: "string", optional: true },
          },
          handler: (ctx: Context<WitnessedRunParams>) => this.witnessedRunProjection(ctx.params),
        },

        governanceMetrics: {
          handler: () => governanceMetrics.snapshot(),
        },
      },
    });
  }

  private async getPool(): Promise<Pool> {
    if (!this.pool) {
      this.pool = new Pool({
        host: process.env.PG_HOST || "localhost",
        port: Number(process.env.PG_PORT || 5432),
        user: process.env.PG_USER || "pguser",
        password: process.env.PG_PASSWORD || "pgpass",
        database: process.env.PG_DB_NAME || "nexus",
        options: "-c search_path=execution",
        max: 5,
        idleTimeoutMillis: 30000,
        connectionTimeoutMillis: 5000,
      });
    }
    return this.pool;
  }

  async stopped(): Promise<void> {
    if (this.pool) await this.pool.end();
  }

  private requireParams(p: WitnessedRunParams): void {
    const workflowInstanceId = (p.workflow_instance_id ?? "").trim();
    const nodeId = (p.node_id ?? "").trim();
    if (!workflowInstanceId || !nodeId) {
      throw new Errors.MoleculerError("workflow_instance_id and node_id are required", 400, "BAD_REQUEST");
    }
  }

  private async fetchRunRow(params: WitnessedRunParams, withProjection: boolean): Promise<any | null> {
    const pool = await this.getPool();
    const workflowInstanceId = params.workflow_instance_id.trim();
    const nodeId = params.node_id.trim();
    const { rows } = await pool.query(
      `SELECT
         r.id AS request_id,
         COALESCE(r.metadata->>'workflow_instance_id', r.business_key) AS workflow_instance_id,
         COALESCE(a.metadata->>'node_id', r.metadata->>'node_id') AS node_id,
         r.metadata->'envelope' AS envelope,
         r.metadata->'manifest' AS manifest,
         r.metadata->'law' AS law,
         r.metadata->'assessment' AS assessment,
         r.metadata->'evidence' AS evidence,
         r.metadata->'replay' AS replay,
         ${withProjection ? "r.updated_at AS updated_at," : ""}
         (SELECT rc.metadata->>'peb_transaction_id' FROM receipts rc WHERE rc.request_id = r.id AND rc.type IN ('PEB_ADMISSION','ADMISSION') ORDER BY rc.issued_at DESC LIMIT 1) AS peb_admission,
         (SELECT rc.metadata->>'conduit_transition_id' FROM receipts rc WHERE rc.request_id = r.id AND rc.type IN ('CONDUIT_TRANSITION','TRANSITION') ORDER BY rc.issued_at DESC LIMIT 1) AS conduit_transition
       FROM requests r
       LEFT JOIN LATERAL (
         SELECT * FROM attempts a0 WHERE a0.request_id = r.id ORDER BY a0.created_at DESC LIMIT 1
       ) a ON true
       WHERE COALESCE(r.metadata->>'workflow_instance_id', r.business_key) = $1
         AND COALESCE(a.metadata->>'node_id', r.metadata->>'node_id') = $2
       LIMIT 1`,
      [workflowInstanceId, nodeId],
    );
    return rows[0] ?? null;
  }

  /**
   * GET /api/execution/witnessed-runs — read-only normalized provenance
   * surface (nullable lineage fields deliberately exposed).
   */
  private async witnessedRuns(params: WitnessedRunParams): Promise<any> {
    this.requireParams(params);
    const row = await this.fetchRunRow(params, false);
    if (!row) throw new Errors.MoleculerError("witnessed run not found", 404, "NOT_FOUND");

    const envelope = row.envelope ?? {};
    const manifest = row.manifest ?? {};
    const law = row.law ?? {};
    const assessment = row.assessment ?? {};
    const evidence = row.evidence ?? {};
    const replay = row.replay ?? {};
    const status = classifyWitnessedRunStatus({
      envelope, manifest, assessment, replay, row,
    });
    governanceMetrics.inc(METRIC_WITNESSED_RUN_STATUS, { status });
    for (const rid of [row.peb_admission, row.conduit_transition]) {
      if (rid != null && !isUuidShape(rid)) governanceMetrics.inc(METRIC_RECEIPT_CORRELATION_INVALID);
    }

    return {
      projection: {
        workflow: { instanceId: params.workflow_instance_id.trim(), nodeId: params.node_id.trim() },
        envelope: {
          id: envelope.id ?? envelope.envelope_id ?? null,
          evaluationFingerprint: envelope.evaluationFingerprint ?? envelope.evaluation_fingerprint ?? null,
          contractId: envelope.contractId ?? envelope.contract_id ?? null,
          contractVersion: envelope.contractVersion ?? envelope.contract_version ?? null,
          contractDigest: envelope.contractDigest ?? envelope.contract_digest ?? null,
        },
        manifest: {
          id: manifest.id ?? manifest.artifactId ?? manifest.artifact_id ?? null,
          version: manifest.version ?? manifest.artifactVersion ?? manifest.artifact_version ?? null,
          digest: manifest.digest ?? manifest.artifactDigest ?? manifest.artifact_digest ?? null,
        },
        law: {
          propositionIds: law.propositionIds ?? law.proposition_ids ?? [],
          doctrineIds: law.doctrineIds ?? law.doctrine_ids ?? [],
          evaluatorId: law.evaluatorId ?? law.evaluator_id ?? null,
        },
        assessment: {
          disposition: assessment.disposition ?? null,
          status: assessment.status ?? null,
          reason: assessment.reason ?? null,
        },
        receipts: { pebAdmission: row.peb_admission, conduitTransition: row.conduit_transition },
        evidence: { ids: evidence.ids ?? evidence.evidence_ids ?? [], fingerprint: evidence.fingerprint ?? evidence.evidence_fingerprint ?? null },
        replay: { fixtureId: replay.fixtureId ?? replay.fixture_id ?? null, status: replay.status ?? null },
        status,
      },
    };
  }

  /**
   * GET /api/execution/witnessed-runs/diagnostics — per-run health summary:
   * authoritative status, enumerated missing lineage elements, receipt
   * correlation validity (W3.05).
   */
  private async witnessedRunDiagnostics(params: WitnessedRunParams): Promise<any> {
    this.requireParams(params);
    const row = await this.fetchRunRow(params, false);
    if (!row) throw new Errors.MoleculerError("witnessed run not found", 404, "NOT_FOUND");

    const envelope = (row.envelope ?? {}) as Record<string, unknown>;
    const manifest = (row.manifest ?? {}) as Record<string, unknown>;
    const assessment = (row.assessment ?? {}) as Record<string, unknown>;
    const evidence = (row.evidence ?? {}) as Record<string, unknown>;
    const replay = (row.replay ?? {}) as Record<string, unknown>;

    const status = classifyWitnessedRunStatus({ envelope, manifest, assessment, replay, row });
    governanceMetrics.inc(METRIC_WITNESSED_RUN_STATUS, { status });

    const envelopeId = (envelope.id ?? envelope.envelope_id) as string | undefined;
    const fingerprint = envelope.evaluationFingerprint ?? envelope.evaluation_fingerprint;
    const manifestId = (manifest.id ?? manifest.artifact_id ?? manifest.artifactId) as string | undefined;
    const evidenceIds = (evidence?.ids ?? evidence?.evidence_ids) as string[] | undefined;
    const missing: string[] = [];
    if (!(envelopeId && fingerprint)) missing.push("envelope");
    if (!manifestId) missing.push("manifest");
    if (!row.peb_admission) missing.push("peb_admission_receipt");
    if (!row.conduit_transition) missing.push("conduit_transition_receipt");
    if (!evidenceIds || evidenceIds.length === 0) missing.push("evidence");

    const correlation = {
      pebAdmission: { id: row.peb_admission ?? null, valid: row.peb_admission ? isUuidShape(row.peb_admission) : null },
      conduitTransition: { id: row.conduit_transition ?? null, valid: row.conduit_transition ? isUuidShape(row.conduit_transition) : null },
    };
    for (const side of [correlation.pebAdmission, correlation.conduitTransition]) {
      if (side.id !== null && side.valid === false) governanceMetrics.inc(METRIC_RECEIPT_CORRELATION_INVALID);
    }

    return {
      projection: {
        workflow: { instanceId: params.workflow_instance_id.trim(), nodeId: params.node_id.trim() },
        request_id: row.request_id,
        status,
        missing_lineage_elements: missing,
        receipt_correlation: correlation,
        envelope_id: envelopeId ?? null,
        evaluation_fingerprint: fingerprint ?? null,
        replay_status: replay.status ?? null,
        assessment: { disposition: assessment.disposition ?? null, status: assessment.status ?? null },
      },
    };
  }

  /**
   * W3.08 — governed downstream projection (versioned; consumers pin to
   * projectionVersion and fail closed on mismatch). Note: the legacy route
   * also sets `Cache-Control: no-store` on the HTTP response; that is a
   * transport concern the gateway owns, not part of the JSON payload.
   */
  private async witnessedRunProjection(params: WitnessedRunParams): Promise<any> {
    this.requireParams(params);
    const row = await this.fetchRunRow(params, true);
    if (!row) throw new Errors.MoleculerError("witnessed run not found", 404, "NOT_FOUND");

    const envelope = (row.envelope ?? {}) as Record<string, unknown>;
    const manifest = (row.manifest ?? {}) as Record<string, unknown>;
    const assessment = (row.assessment ?? {}) as Record<string, unknown>;
    const evidence = (row.evidence ?? {}) as Record<string, unknown>;
    const replay = (row.replay ?? {}) as Record<string, unknown>;

    const envelopeId = (envelope.id ?? envelope.envelope_id ?? null) as string | null;
    const evaluationFingerprint = (envelope.evaluationFingerprint ?? envelope.evaluation_fingerprint ?? null) as string | null;
    const manifestId = (manifest.id ?? manifest.artifactId ?? manifest.artifact_id ?? null) as string | null;
    const evidenceIds = (evidence.ids ?? evidence.evidence_ids ?? []) as string[];
    const pebAdmission = row.peb_admission ?? null;
    const conduitTransition = row.conduit_transition ?? null;

    const status = classifyWitnessedRunStatus({ envelope, manifest, assessment, replay, row });

    const missingLineage: string[] = [];
    if (!envelopeId) missingLineage.push("envelope_id");
    if (!evaluationFingerprint) missingLineage.push("evaluation_fingerprint");
    if (!manifestId) missingLineage.push("manifest_id");
    if (!pebAdmission) missingLineage.push("peb_admission_receipt");
    if (!conduitTransition) missingLineage.push("conduit_transition_receipt");
    if (!evidenceIds || evidenceIds.length === 0) missingLineage.push("evidence_ids");

    return {
      projectionVersion: WITNESSED_RUN_PROJECTION_VERSION,
      projection: "witnessed-run",
      generatedAt: new Date().toISOString(),
      sourceUpdatedAt: row.updated_at ?? null,
      workflow: { instanceId: params.workflow_instance_id.trim(), nodeId: params.node_id.trim() },
      request: { id: row.request_id ?? null },
      identities: {
        envelopeId,
        evaluationFingerprint,
        manifestId,
        pebAdmissionReceiptId: pebAdmission,
        conduitTransitionReceiptId: conduitTransition,
        evidenceIds,
      },
      assessment: {
        disposition: assessment.disposition ?? null,
        status: assessment.status ?? null,
      },
      replay: { fixtureId: replay.fixtureId ?? replay.fixture_id ?? null, status: replay.status ?? null },
      status,
      missingLineage,
    };
  }
}
