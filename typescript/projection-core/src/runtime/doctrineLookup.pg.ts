import type {
  DoctrineLookup,
  DoctrineLookupRequest,
  DoctrineLookupResult,
  DoctrineRecord,
} from './doctrineLookup.js';

/**
 * W3.02 — Productionize deterministic doctrine lookup (PG-backed).
 *
 * Implements the W2.02 `DoctrineLookup` interface against a PostgreSQL
 * doctrine-projection store (`resolution.doctrine_projection` shape):
 *
 *   doctrine_id      text  -- the envelope's law.doctrine_ids[] value
 *   doctrine_version int
 *   kind             text  -- 'doctrine' | 'proposition' | 'posture'
 *   digest           text  -- sha256 over canonical doctrine text
 *   source_ref       text  -- architect decision / record UUID (provenance)
 *   effective_from   timestamptz
 *   superseded_at    timestamptz NULL
 *
 * Design invariants:
 *  - SERVER-OWNED CONFIGURATION: the queryable (pg Pool/Client-like) and the
 *    SQL are supplied by the host service; this module owns no credentials.
 *  - DETERMINISTIC: identical inputs produce identical results — same
 *    effective-date resolution semantics as InMemoryDoctrineLookup
 *    (max effective_from <= asOf; not superseded at asOf; higher version
 *    wins on tie). No wall-clock dependence beyond the caller-supplied asOf.
 *  - FAIL-CLOSED: any query error, timeout, or malformed row resolves to
 *    status `unknown` (reason `lookup_error`) — never throws, never
 *    resolves-on-error. Callers using assertBlockingLookup will refuse.
 *  - REPLACEABLE PEB INTERFACE: callers depend only on `DoctrineLookup`;
 *    a future PEB-backed implementation drops in without caller changes.
 *    peb.decisions remains dormant — this class performs READS only.
 *
 * The `queryable` is duck-typed (`query(sql, params) -> { rows }`) so tests
 * can inject a mock and hosts can inject pg.Pool / pg.Client / a pool proxy.
 */

/** Minimal duck-type of a pg pool/client (only .query is required). */
export interface PgQueryable {
  query(sql: string, params?: unknown[]): Promise<{ rows: any[]; rowCount?: number | null }>;
}

export interface PgDoctrineLookupOptions {
  /** Duck-typed pg pool/client. Server-owned configuration. */
  queryable: PgQueryable;
  /** Optional SQL override (must return the canonical column set). */
  sql?: string;
  /** Lookup timeout in ms (fail-closed on exceed). Default 3000. */
  timeoutMs?: number;
  /** Default kind when the store row lacks one (default 'doctrine'). */
  defaultKind?: DoctrineRecord['kind'];
}

const DEFAULT_SQL = `
SELECT
  doctrine_id::text        AS id,
  doctrine_version         AS version,
  content_digest           AS digest,
  effective_from           AS effective_from,
  superseded_by            AS superseded_at,
  source_ref               AS source_decision_id,
  source_kind              AS kind
FROM resolution.doctrine_projection
WHERE doctrine_id::text = $1
ORDER BY effective_from DESC, doctrine_version DESC`;

export class PgDoctrineLookup implements DoctrineLookup {
  private readonly queryable: PgQueryable;
  private readonly sql: string;
  private readonly timeoutMs: number;
  private readonly defaultKind: DoctrineRecord['kind'];

  constructor(options: PgDoctrineLookupOptions) {
    if (!options || !options.queryable || typeof options.queryable.query !== 'function') {
      throw new Error('PgDoctrineLookup: queryable with .query() is required');
    }
    this.queryable = options.queryable;
    this.sql = options.sql ?? DEFAULT_SQL;
    this.timeoutMs = options.timeoutMs ?? 3000;
    this.defaultKind = options.defaultKind ?? 'doctrine';
  }

  async lookup(request: DoctrineLookupRequest, _signal?: AbortSignal): Promise<DoctrineLookupResult> {
    const started = performance.now();

    // ── Refusal: malformed request (fail-closed, mirrors InMemory) ──
    if (!request || typeof request.stableId !== 'string' || !request.stableId || !request.asOf) {
      return {
        status: 'refusal',
        consulted: false,
        latencyMs: this.elapsed(started),
        reason: 'stable_id_and_as_of_required',
      };
    }

    // ── Query with hard timeout (fail-closed) ──
    let rows: any[];
    try {
      const result = await this.withTimeout(
        this.queryable.query(this.sql, [request.stableId]),
        this.timeoutMs,
        'lookup_timeout',
      );
      // pg-style queryables return { rows, rowCount }; unwrap defensively.
      rows = Array.isArray(result) ? result : (result?.rows ?? null);
    } catch (err: any) {
      return {
        status: 'unknown',
        consulted: true,
        latencyMs: this.elapsed(started),
        reason: err && err.reason === 'lookup_timeout' ? 'lookup_timeout' : 'lookup_error',
      };
    }

    // ── Malformed result shape (fail-closed) ──
    if (!Array.isArray(rows)) {
      return { status: 'unknown', consulted: true, latencyMs: this.elapsed(started), reason: 'lookup_error' };
    }

    // ── Effective-date resolution (deterministic, same as InMemory) ──
    const candidates = rows
      .map((r) => this.toRecord(r))
      .filter((rec): rec is DoctrineRecord => rec !== null);

    if (candidates.length === 0) {
      return {
        status: 'unknown',
        consulted: true,
        latencyMs: this.elapsed(started),
        reason: 'stable_id_not_found',
      };
    }

    const active = candidates
      .filter(
        (rec) =>
          rec.effectiveFrom <= request.asOf &&
          (rec.supersededAt == null || rec.supersededAt > request.asOf),
      )
      .sort(
        (left, right) =>
          right.effectiveFrom.localeCompare(left.effectiveFrom) || right.version - left.version,
      )[0];

    if (!active) {
      return {
        status: 'stale',
        consulted: true,
        latencyMs: this.elapsed(started),
        reason: 'stable_id_not_effective_at_as_of',
      };
    }

    return {
      status: 'resolved',
      consulted: true,
      latencyMs: this.elapsed(started),
      record: structuredClone(active),
    };
  }

  /** Map a store row to a DoctrineRecord; null on malformed rows (row skipped, fail-closed). */
  private toRecord(r: any): DoctrineRecord | null {
    try {
      const id = r.id ?? r.doctrine_id;
      const version = Number(r.version ?? r.doctrine_version);
      const digest = r.digest ?? r.content_digest;
      const effectiveFrom = this.toIso(r.effective_from);
      if (!id || !Number.isFinite(version) || !digest || !effectiveFrom) return null;
      const kindRaw = (r.kind ?? this.defaultKind) as string;
      const kind = (['doctrine', 'proposition', 'posture'].includes(kindRaw)
        ? kindRaw
        : this.defaultKind) as DoctrineRecord['kind'];
      const rec: DoctrineRecord = {
        kind,
        id: String(id),
        version,
        digest: (String(digest).startsWith('sha256:')
          ? (String(digest) as `sha256:${string}`)
          : (`sha256:${String(digest)}` as `sha256:${string}`)),
        effectiveFrom,
        supersededAt: r.superseded_at ? this.toIso(r.superseded_at) : null,
        sourceDecisionId: String(r.source_decision_id ?? r.source_ref ?? ''),
      };
      return rec;
    } catch {
      return null;
    }
  }

  /** pg timestamptz arrives as Date or string; normalize to ISO string. */
  private toIso(v: any): string | null {
    if (v == null) return null;
    if (v instanceof Date) return v.toISOString();
    const s = String(v);
    return s || null;
  }

  private withTimeout<T>(p: Promise<T>, ms: number, reason: string): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const t = setTimeout(() => {
        const e: any = new Error(reason);
        e.reason = reason;
        reject(e);
      }, ms);
      p.then(
        (v) => {
          clearTimeout(t);
          resolve(v);
        },
        (e) => {
          clearTimeout(t);
          reject(e);
        },
      );
    });
  }

  private elapsed(started: number): number {
    return Math.max(0, Math.round((performance.now() - started) * 1000) / 1000);
  }
}
