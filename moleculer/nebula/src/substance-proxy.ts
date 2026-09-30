// src/substance-proxy.ts — Thin HTTP client for routing segment-set reads to
// substance (:3115).
//
// Why this exists: substance owns the segment-set scheme (nebula.segment_sets
// and its domain join tables), including the Redis read-through cache and the
// LISTEN/NOTIFY `segment_expired` invalidation listener. Querying those tables
// directly from nebula-srv would (a) duplicate the resolution logic and (b)
// race the cache discipline — so per the same doctrine that routes assembly's
// nebula-domain reads through nebula-proxy (Assembly Rewrite thread,
// 2026-07-24), segment-set reads route through substance.
//
// READ-ONLY by design: no write helpers. Segment sets are created/linked by
// the ingest tooling through substance itself; nebula-srv surfaces the
// evidence for domain reads only.
//
// Substance returns plain JSON (not the Paged<T> envelope nebula-srv emits),
// so responses pass through after shape normalization to camelCase.

const SUBSTANCE_BASE =
  process.env.SUBSTANCE_BASE_URL || 'http://localhost:3115';

export interface SubstanceSegmentMember {
  segmentId: string;
  ordinal: number;
  note: string | null;
  conversationId: string | null;
  startBlockIndex: number | null;
  endBlockIndex: number | null;
  segmentType: string | null;
  title: string | null;
}

/** Fetch JSON from substance; map transport/HTTP failures to Error with status context. */
export async function fetchSubstance(path: string): Promise<any> {
  let res: Response;
  try {
    res = await fetch(`${SUBSTANCE_BASE}${path}`, {
      headers: { accept: 'application/json' },
      signal: AbortSignal.timeout(10_000),
    });
  } catch (err: any) {
    throw new Error(
      `substance unreachable at ${SUBSTANCE_BASE}: ${err?.message || err}`,
    );
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`substance ${res.status}: ${body.slice(0, 300)}`);
  }
  return res.json();
}

/** snake_case → camelCase, recursive, for substance response shapes. */
export function substanceToCamel(obj: any): any {
  if (Array.isArray(obj)) return obj.map(substanceToCamel);
  if (obj && typeof obj === 'object') {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(obj)) {
      out[k.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())] =
        v && typeof v === 'object' ? substanceToCamel(v) : v;
    }
    return out;
  }
  return obj;
}
