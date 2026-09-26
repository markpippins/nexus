/**
 * W3.05 — Governance observability: in-process metrics registry.
 *
 * Server-derived metrics for the governance plane. No external dependencies;
 * counters and latency aggregates only. All metric families correlate to
 * immutable identities (envelope id, receipt ids) — never payloads.
 *
 * Families:
 *  - witnessed_run_status_total{status}      — classifier verdicts (W3.03 vocabulary)
 *  - doctrine_lookup_total{status,reason}    — doctrine lookup outcomes (W3.02 taxonomy)
 *  - doctrine_lookup_latency_ms_sum/_count   — lookup latency aggregates
 *  - receipt_correlation_invalid_total       — malformed correlation ids seen
 *
 * peb.decisions stays dormant; PEB and Conduit authorities remain separate.
 */

export class MetricsRegistry {
  private counters = new Map<string, number>();
  private latency = new Map<string, { sum: number; count: number }>();

  /** Increment a counter by 1 (or by `by`). */
  inc(name: string, labels: Record<string, string> = {}, by = 1): void {
    const key = this.key(name, labels);
    this.counters.set(key, (this.counters.get(key) ?? 0) + by);
  }

  /** Record one latency observation (ms). */
  observeLatency(name: string, labels: Record<string, string>, ms: number): void {
    const key = this.key(name, labels);
    const cur = this.latency.get(key) ?? { sum: 0, count: 0 };
    cur.sum += ms;
    cur.count += 1;
    this.latency.set(key, cur);
  }

  /** Current value of a counter (0 if absent). */
  get(name: string, labels: Record<string, string> = {}): number {
    return this.counters.get(this.key(name, labels)) ?? 0;
  }

  /** Latency aggregate for a family+labels. */
  latencyFor(name: string, labels: Record<string, string> = {}): { sum: number; count: number; avg: number } {
    const cur = this.latency.get(this.key(name, labels)) ?? { sum: 0, count: 0 };
    return { ...cur, avg: cur.count === 0 ? 0 : Math.round((cur.sum / cur.count) * 1000) / 1000 };
  }

  /** Snapshot all metrics as a JSON-serializable object (for GET /metrics). */
  snapshot(): Record<string, unknown> {
    const counters: Record<string, number> = {};
    for (const [k, v] of this.counters) counters[k] = v;
    const latencies: Record<string, { sum: number; count: number; avg: number }> = {};
    for (const [k, v] of this.latency) latencies[k] = { ...v, avg: v.count === 0 ? 0 : Math.round((v.sum / v.count) * 1000) / 1000 };
    return { counters, latencies, generatedAt: new Date().toISOString() };
  }

  /** Reset all metrics (tests). */
  reset(): void {
    this.counters.clear();
    this.latency.clear();
  }

  private key(name: string, labels: Record<string, string>): string {
    const parts = Object.keys(labels).sort().map((k) => `${k}=${String(labels[k]).replace(/[^\w.-]/g, '_')}`);
    return parts.length ? `${name}{${parts.join(',')}}` : name;
  }
}

/** Process-wide registry used by execution-srv routes. */
export const governanceMetrics = new MetricsRegistry();

/** Canonical metric family names. */
export const METRIC_WITNESSED_RUN_STATUS = 'witnessed_run_status_total';
export const METRIC_DOCTRINE_LOOKUP = 'doctrine_lookup_total';
export const METRIC_DOCTRINE_LOOKUP_LATENCY = 'doctrine_lookup_latency_ms';
export const METRIC_RECEIPT_CORRELATION_INVALID = 'receipt_correlation_invalid_total';

/** Loose UUID shape check mirroring resolution.is_uuid(text) semantics. */
export function isUuidShape(value: unknown): boolean {
  if (typeof value !== 'string' || value.length === 0) return false;
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
