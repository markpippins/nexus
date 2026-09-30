// ═══════════════════════════════════════════════════════════════════════
//  Block Segmentation — Redis Layer
//
//  Volatile session-memory caching for block segmentation.
//  All Redis state is RECOMPUTABLE from Postgres — never a source of
//  truth. On cache miss or invalidation, rebuild from the database.
//
//  Key patterns (from SPEC section 4):
//    nebula:session:{conversation_id}                     → Hash
//    nebula:snapshot:{id}:block:{block_id}                → Hash
//    nebula:snapshot:{id}:segment_candidates              → Hash
//    nebula:graph:{id}:out:{node_id}                      → SET
//    nebula:graph:{id}:in:{node_id}                       → SET
//    nebula:snapshot:{id}:bp_projection:{target}          → JSON string
//
//  Usage:
//    import * as bsRedis from './services/block-segmentation-redis.service';
//    bsRedis.initRedis();
//    await bsRedis.cacheBlocks(snapshotId, blocks);
//    const cached = await bsRedis.getCachedSession(conversationId);
// ═══════════════════════════════════════════════════════════════════════

import Redis from 'ioredis';
import { Pool } from 'pg';

// ── Key helpers (prefix matches spec) ────────────────────────────

/** Session context for a conversation (Hash). */
const KEY_SESSION = (convId: string) => `nebula:session:${convId}`;

/** Single block metadata (Hash). */
const KEY_BLOCK = (snapId: string, blockId: string) =>
  `nebula:snapshot:${snapId}:block:${blockId}`;

/** Pending segment candidates (Hash of candidate-id → JSON). */
const KEY_CANDIDATES = (snapId: string) =>
  `nebula:snapshot:${snapId}:segment_candidates`;

/** Forward adjacency: which edges this node emits (SET). */
const KEY_GRAPH_OUT = (snapId: string, nodeId: string) =>
  `nebula:graph:${snapId}:out:${nodeId}`;

/** Reverse adjacency: which edges point to this node (SET). */
const KEY_GRAPH_IN = (snapId: string, nodeId: string) =>
  `nebula:graph:${snapId}:in:${nodeId}`;

/** Cached BP projection (JSON string). */
const KEY_PROJECTION = (snapId: string, target: string) =>
  `nebula:snapshot:${snapId}:bp_projection:${target}`;

/** Pattern to match all keys for a given snapshot (used for bulk invalidation). */
const PATTERN_SNAPSHOT = (snapId: string) =>
  `nebula:snapshot:${snapId}:*`;

// ── Redis connection (singleton) ─────────────────────────────────

let redis: Redis | null = null;

/**
 * Initialize the Redis client. Uses REDIS_URL env var or falls back to
 * redis://localhost:6379. Lazy-connect so startup doesn't fail if
 * Redis is temporarily down.
 */
export function initRedis(): Redis {
  const url = process.env.REDIS_URL || 'redis://localhost:6379';
  redis = new Redis(url, {
    maxRetriesPerRequest: 3,
    retryStrategy(times: number) {
      // Always retry with capped exponential backoff. Returning null would
      // permanently close the client (ioredis semantics) and prevent any
      // recovery after a transient Redis outage — which left nebula-srv's
      // block-segmentation cache silently broken (every cache op failed,
      // routes fell back to PG on every request). Same fix as role-memory-srv
      // (commit b099522) and tackle-mcp.
      return Math.min(times * 200, 2000);
    },
    lazyConnect: true,
  });

  redis.on('error', (err) => {
    console.error('[redis] block-segmentation error:', err.message);
  });

  // Attempt to connect (non-blocking; lazy connect means it'll retry)
  redis.connect().catch((err) => {
    console.warn('[redis] block-segmentation initial connect failed:', err.message);
  });

  return redis;
}

/** Get the Redis client (throws if not initialized). */
export function getRedis(): Redis {
  if (!redis) throw new Error('Redis not initialized. Call initRedis() first.');
  return redis;
}

/** Gracefully close the Redis connection. */
export async function closeRedis(): Promise<void> {
  if (redis) {
    await redis.quit();
    redis = null;
  }
}

// ── Inbox Pointer (per-role watermark for unread messages) ────────

/**
 * Key pattern for inbox pointers.
 * Stores the ISO timestamp of the last-seen record per role.
 * All state is RECOMPUTABLE — on Redis loss, agents re-read from the beginning.
 */
const KEY_INBOX_POINTER = (role: string) => `inbox:pointer:${role}`;

/**
 * Get the inbox pointer for a role.
 * Returns the ISO timestamp of the last-seen record, or null if no pointer exists.
 */
export async function getInboxPointer(role: string): Promise<string | null> {
  if (!redis) return null;
  try {
    return await redis.get(KEY_INBOX_POINTER(role));
  } catch {
    return null;
  }
}

/**
 * Set the inbox pointer for a role.
 * @param role - The role name (e.g., "architect", "engineer")
 * @param timestamp - ISO timestamp of the last-seen record
 */
export async function setInboxPointer(role: string, timestamp: string): Promise<void> {
  if (!redis) return;
  await redis.set(KEY_INBOX_POINTER(role), timestamp);
}

/**
 * Get all inbox pointers (for debugging/monitoring).
 */
export async function getAllInboxPointers(): Promise<Record<string, string | null>> {
  if (!redis) return {};
  const keys = await redis.keys('inbox:pointer:*');
  const result: Record<string, string | null> = {};
  for (const key of keys) {
    const role = key.replace('inbox:pointer:', '');
    result[role] = await redis.get(key);
  }
  return result;
}

// ── Role Memory Invalidation (D-2026-08-16-009 R4) ───────────────

const KEY_ROLE_INDEX = (role: string) => `mem:idx:${role}`;

/**
 * Invalidate a role's procedure-card index (targeted, no broad flush).
 *
 * D-2026-08-16-009 R4: on a role lifecycle change (revoke / expire /
 * exhaustion) the role's cached procedure index is invalidated so the next
 * turn re-reads from role-memory-srv. Only the single `mem:idx:{role}` key
 * is deleted — shared `mem:proc:{slug}` cards and other roles' indices are
 * untouched.
 */
export async function invalidateRoleMemory(role: string): Promise<void> {
  if (!redis) return;
  try {
    await redis.del(KEY_ROLE_INDEX(role));
  } catch (err: any) {
    // Non-fatal — role-memory-srv repopulates on its next sync.
    console.warn('[redis] role-memory invalidation failed:', err.message);
  }
}

// ── Session Cache (volatile per-conversation context) ────────────

/** Fields stored in the session hash. */
export interface SessionData {
  conversationId: string;
  activeSnapshotId: string | null;
  mode: 'view' | 'segment' | 'diff';
  userId: string;
  updatedAt: string;
}

/** Store session context in Redis as a Hash. */
export async function cacheSession(
  convId: string,
  data: Partial<SessionData>,
): Promise<void> {
  const r = getRedis();
  const fields: Record<string, string> = {};
  for (const [key, value] of Object.entries(data)) {
    if (value !== undefined) {
      fields[key] = typeof value === 'string' ? value : JSON.stringify(value);
    }
  }
  fields.updatedAt = new Date().toISOString();
  await r.hset(KEY_SESSION(convId), fields);
  // Expire inactive sessions after 1 hour
  await r.expire(KEY_SESSION(convId), 3600);
}

/** Read session context from Redis (null on miss). */
export async function getCachedSession(
  convId: string,
): Promise<Partial<SessionData> | null> {
  const r = getRedis();
  const raw = await r.hgetall(KEY_SESSION(convId));
  if (!raw || Object.keys(raw).length === 0) return null;
  return {
    conversationId: raw.conversationId,
    activeSnapshotId: raw.activeSnapshotId || null,
    mode: (raw.mode as SessionData['mode']) || 'view',
    userId: raw.userId || 'unknown',
    updatedAt: raw.updatedAt || '',
  };
}

/** Delete session context. */
export async function invalidateSession(convId: string): Promise<void> {
  const r = getRedis();
  await r.del(KEY_SESSION(convId));
}

// ── Block Cache (individual block metadata by snapshot+block) ────

/** Store a single block's metadata as a Redis Hash. */
export async function cacheBlock(
  snapId: string,
  block: Record<string, any>,
): Promise<void> {
  const r = getRedis();
  const key = KEY_BLOCK(snapId, block.id || block.blockId);
  const fields: Record<string, string> = {};
  for (const [k, v] of Object.entries(block)) {
    if (v !== undefined && v !== null) {
      fields[k] = typeof v === 'string' ? v : String(v);
    }
  }
  await r.hset(key, fields);
  // Blocks live for 2 hours (conversation sessions rarely last longer)
  await r.expire(key, 7200);
}

/** Bulk-store blocks (pipeline for performance). */
export async function cacheBlocks(
  snapId: string,
  blocks: Record<string, any>[],
): Promise<void> {
  const r = getRedis();
  const pipeline = r.pipeline();
  for (const block of blocks) {
    const key = KEY_BLOCK(snapId, block.id || block.blockId);
    const fields: Record<string, string> = {};
    for (const [k, v] of Object.entries(block)) {
      if (v !== undefined && v !== null) {
        fields[k] = typeof v === 'string' ? v : String(v);
      }
    }
    pipeline.hset(key, fields);
    pipeline.expire(key, 7200);
  }
  await pipeline.exec();
}

/** Read a single block from cache (null on miss). */
export async function getCachedBlock(
  snapId: string,
  blockId: string,
): Promise<Record<string, string> | null> {
  const r = getRedis();
  const raw = await r.hgetall(KEY_BLOCK(snapId, blockId));
  if (!raw || Object.keys(raw).length === 0) return null;
  return raw;
}

/** Delete all block keys for a given snapshot. */
export async function invalidateBlocks(snapId: string): Promise<void> {
  const r = getRedis();
  // Scan for matching keys and delete them
  let cursor = '0';
  do {
    const [nextCursor, keys] = await r.scan(
      cursor,
      'MATCH',
      `nebula:snapshot:${snapId}:block:*`,
      'COUNT',
      100,
    );
    cursor = nextCursor;
    if (keys.length > 0) {
      await r.del(...keys);
    }
  } while (cursor !== '0');
}

// ── Segment Candidate Cache (pending segments before commit) ─────

/** Store a pending segment candidate. */
export async function cacheCandidate(
  snapId: string,
  candidateId: string,
  data: Record<string, any>,
): Promise<void> {
  const r = getRedis();
  await r.hset(
    KEY_CANDIDATES(snapId),
    candidateId,
    JSON.stringify(data),
  );
  await r.expire(KEY_CANDIDATES(snapId), 3600);
}

/** Read all pending candidates for a snapshot. */
export async function getCachedCandidates(
  snapId: string,
): Promise<Record<string, any>> {
  const r = getRedis();
  const raw = await r.hgetall(KEY_CANDIDATES(snapId));
  if (!raw || Object.keys(raw).length === 0) return {};
  const result: Record<string, any> = {};
  for (const [id, json] of Object.entries(raw)) {
    try {
      result[id] = JSON.parse(json);
    } catch {
      result[id] = json;
    }
  }
  return result;
}

/** Remove a specific candidate by id. */
export async function removeCachedCandidate(
  snapId: string,
  candidateId: string,
): Promise<void> {
  const r = getRedis();
  await r.hdel(KEY_CANDIDATES(snapId), candidateId);
}

/** Delete all candidates for a snapshot. */
export async function invalidateCandidates(snapId: string): Promise<void> {
  const r = getRedis();
  await r.del(KEY_CANDIDATES(snapId));
}

// ── Graph Adjacency Cache (forward + reverse edge sets) ──────────

/**
 * Rebuild graph adjacency from a list of harvest references.
 * Wipes existing graph keys for this snapshot and replaces them.
 */
export async function cacheGraphFromReferences(
  snapId: string,
  references: Array<{
    id: string;
    source_block_id?: string | null;
    source_segment_id?: string | null;
    target_block_id?: string | null;
    target_segment_id?: string | null;
  }>,
): Promise<void> {
  const r = getRedis();
  const pipeline = r.pipeline();

  // Collect all nodes that appear as sources or targets
  const nodeIds = new Set<string>();
  const edges: Array<{ source: string; target: string; edgeId: string }> = [];

  for (const ref of references) {
    const sourceId = ref.source_block_id || ref.source_segment_id;
    const targetId = ref.target_block_id || ref.target_segment_id;
    if (!sourceId || !targetId) continue;

    nodeIds.add(sourceId);
    nodeIds.add(targetId);
    edges.push({ source: sourceId, target: targetId, edgeId: ref.id });
  }

  // Wipe existing graph keys for this snapshot (SCAN-based delete)
  let cursor = '0';
  do {
    const [nextCursor, keys] = await r.scan(
      cursor,
      'MATCH',
      `nebula:graph:${snapId}:*`,
      'COUNT',
      100,
    );
    cursor = nextCursor;
    if (keys.length > 0) {
      pipeline.del(...keys);
    }
  } while (cursor !== '0');

  // Build new adjacency sets
  const outEdges = new Map<string, string[]>();
  const inEdges = new Map<string, string[]>();

  for (const edge of edges) {
    if (!outEdges.has(edge.source)) outEdges.set(edge.source, []);
    outEdges.get(edge.source)!.push(edge.edgeId);

    if (!inEdges.has(edge.target)) inEdges.set(edge.target, []);
    inEdges.get(edge.target)!.push(edge.edgeId);
  }

  // Store adjacency sets
  for (const [nodeId, edgeIds] of outEdges) {
    if (edgeIds.length > 0) {
      pipeline.sadd(KEY_GRAPH_OUT(snapId, nodeId), ...edgeIds);
    }
  }
  for (const [nodeId, edgeIds] of inEdges) {
    if (edgeIds.length > 0) {
      pipeline.sadd(KEY_GRAPH_IN(snapId, nodeId), ...edgeIds);
    }
  }

  await pipeline.exec();
}

/** Get outgoing edge IDs for a graph node. */
export async function getCachedGraphOutEdges(
  snapId: string,
  nodeId: string,
): Promise<string[]> {
  const r = getRedis();
  return await r.smembers(KEY_GRAPH_OUT(snapId, nodeId));
}

/** Get incoming edge IDs for a graph node. */
export async function getCachedGraphInEdges(
  snapId: string,
  nodeId: string,
): Promise<string[]> {
  const r = getRedis();
  return await r.smembers(KEY_GRAPH_IN(snapId, nodeId));
}

/** Delete all graph adjacency keys for a snapshot. */
export async function invalidateGraph(snapId: string): Promise<void> {
  const r = getRedis();
  let cursor = '0';
  do {
    const [nextCursor, keys] = await r.scan(
      cursor,
      'MATCH',
      `nebula:graph:${snapId}:*`,
      'COUNT',
      100,
    );
    cursor = nextCursor;
    if (keys.length > 0) {
      await r.del(...keys);
    }
  } while (cursor !== '0');
}

// ── BP Projection Cache (cached blueprint view) ──────────────────

/** Cache the BP projection result. */
export async function cacheProjection(
  snapId: string,
  target: string,
  data: any,
): Promise<void> {
  const r = getRedis();
  const key = KEY_PROJECTION(snapId, target);
  await r.set(key, JSON.stringify(data), 'EX', 3600);
}

/** Read cached BP projection (null on miss). */
export async function getCachedProjection(
  snapId: string,
  target: string = 'BP',
): Promise<any | null> {
  const r = getRedis();
  const raw = await r.get(KEY_PROJECTION(snapId, target));
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** Invalidate BP projection cache for one or all targets. */
export async function invalidateProjection(
  snapId: string,
  target?: string,
): Promise<void> {
  const r = getRedis();
  if (target) {
    await r.del(KEY_PROJECTION(snapId, target));
  } else {
    // Delete all projection targets for this snapshot
    let cursor = '0';
    do {
      const [nextCursor, keys] = await r.scan(
        cursor,
        'MATCH',
        `nebula:snapshot:${snapId}:bp_projection:*`,
        'COUNT',
        100,
      );
      cursor = nextCursor;
      if (keys.length > 0) {
        await r.del(...keys);
      }
    } while (cursor !== '0');
  }
}

// ── Bulk Invalidation ────────────────────────────────────────────

/** Invalidate ALL cached data for a given snapshot. */
export async function invalidateSnapshot(snapId: string): Promise<void> {
  const r = getRedis();
  let cursor = '0';
  do {
    const [nextCursor, keys] = await r.scan(
      cursor,
      'MATCH',
      PATTERN_SNAPSHOT(snapId),
      'COUNT',
      200,
    );
    cursor = nextCursor;
    if (keys.length > 0) {
      await r.del(...keys);
    }
  } while (cursor !== '0');
  // Also invalidate graph keys
  await invalidateGraph(snapId);
}

/** Invalidate ALL cached data for a conversation (session + all its snapshots). */
export async function invalidateConversation(
  convId: string,
  snapshotIds: string[],
): Promise<void> {
  // Delete session
  await invalidateSession(convId);
  // Delete all snapshot data
  for (const snapId of snapshotIds) {
    await invalidateSnapshot(snapId);
  }
}

// ── Recompute (rebuild Redis from Postgres) ──────────────────────

/**
 * Recompute the block cache for a snapshot from the database.
 * Used after cache invalidation on read-miss.
 */
export async function recomputeBlocksFromDb(
  pool: Pool,
  snapId: string,
): Promise<void> {
  const { rows: blocks } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, block_index, parent_turn_id,
            parent_block_id, block_type, content_md, content_hash,
            dom_path, dom_fingerprint, first_line_no, last_line_no,
            to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS created_at
     FROM nebula.conversation_blocks
     WHERE snapshot_id = $1
     ORDER BY block_index`,
    [snapId],
  );
  if (blocks.length > 0) {
    await cacheBlocks(snapId, blocks);
  }
}

/**
 * Recompute the BP projection cache from the database.
 */
export async function recomputeProjectionFromDb(
  pool: Pool,
  snapId: string,
  target: string = 'BP',
): Promise<any> {
  const { rows: blocks } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, block_index, parent_turn_id,
            parent_block_id, block_type, content_md, content_hash,
            dom_path, dom_fingerprint, first_line_no, last_line_no
     FROM nebula.conversation_blocks
     WHERE snapshot_id = $1
     ORDER BY block_index`,
    [snapId],
  );

  const { rows: segments } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, start_block_id, end_block_id,
            start_block_index, end_block_index, segment_type, state, source,
            title, notes_md, created_by, created_at
     FROM nebula.segments
     WHERE snapshot_id = $1
     ORDER BY start_block_index`,
    [snapId],
  );

  const { rows: overrides } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, target_type, target_id,
            projection_target, override_type, reason_code, notes_md,
            source, created_by, created_at
     FROM nebula.projection_overrides
     WHERE snapshot_id = $1
       AND projection_target = $2
     ORDER BY created_at`,
    [snapId, target],
  );

  const projection = { blocks, segments, overrides };
  await cacheProjection(snapId, target, projection);
  return projection;
}

/**
 * Recompute graph adjacency from harvest references in the database.
 */
export async function recomputeGraphFromDb(
  pool: Pool,
  snapId: string,
): Promise<void> {
  const { rows: references } = await pool.query(
    `SELECT id, source_block_id, source_segment_id,
            target_block_id, target_segment_id
     FROM nebula.harvest_references
     WHERE snapshot_id = $1`,
    [snapId],
  );
  await cacheGraphFromReferences(snapId, references);
}
