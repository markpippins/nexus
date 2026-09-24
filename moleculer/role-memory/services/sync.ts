import { fetchAllActiveMemory } from "./store";
import { getRedis, PROC_KEY, IDX_KEY, META_UPDATED_KEY } from "./store";

export interface ProcedureIndexEntry {
  slug: string;
  summary: string;
  tags: string[];
}

export interface ProcedureCard {
  slug: string;
  title: string;
  summary: string;
  body_md: string;
  tags: string[];
  triggers: string[];
  mcp_tools: string[];
  roles: string[];
  updated_at: string;
}

/**
 * Full sync: read all active procedures from PG, write to Redis.
 * VERBATIM PORT of the incumbent's sync.ts syncAll() — called by the
 * refresh action (and available for boot auto-sync in the runner entry).
 */
export async function syncAll(): Promise<{
  procedures: number;
  roleIndices: number;
  timestamp: string;
}> {
  const redis = getRedis();
  const memMap = await fetchAllActiveMemory();

  const now = new Date().toISOString();
  const pipeline = redis.pipeline();

  // Build role→index lookups
  const roleIdx = new Map<string, ProcedureIndexEntry[]>();

  for (const [slug, { procedure, roles }] of memMap) {
    const card: ProcedureCard = {
      slug,
      title: procedure.title,
      summary: procedure.summary,
      body_md: procedure.body_md,
      tags: procedure.tags,
      triggers: procedure.triggers,
      mcp_tools: procedure.mcp_tools,
      roles,
      updated_at: procedure.updated_at,
    };

    // Set mem:proc:{slug}
    pipeline.set(PROC_KEY(slug), JSON.stringify(card));

    // Add to each role's index
    for (const role of roles) {
      const idx = roleIdx.get(role) || [];
      idx.push({ slug, summary: procedure.summary, tags: procedure.tags });
      roleIdx.set(role, idx);
    }
  }

  // Write role indices
  for (const [role, entries] of roleIdx) {
    pipeline.set(IDX_KEY(role), JSON.stringify(entries));
  }

  // Write last-updated timestamp
  pipeline.set(META_UPDATED_KEY, now);

  const results = await pipeline.exec();
  // ioredis pipeline.exec() resolves with [error, reply][] and does NOT
  // throw on per-command failure. Surface write failures so /refresh
  // returns HTTP 500 instead of fake success with PG-read counts.
  if (results) {
    const failures = results.filter(([err]) => err !== null);
    if (failures.length > 0) {
      const first = failures[0][0] as Error;
      throw new Error(
        `Redis pipeline write failed: ${failures.length}/${results.length} commands failed. First error: ${first.message}`
      );
    }
  }

  return {
    procedures: memMap.size,
    roleIndices: roleIdx.size,
    timestamp: now,
  };
}
