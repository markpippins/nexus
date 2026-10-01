// ═══════════════════════════════════════════════════════════════════════
//  Block Segmentation Service
//  Handles 10 endpoints for interactive block-level segmentation of
//  harvested conversation transcripts.
// ═══════════════════════════════════════════════════════════════════════

import { Pool } from 'pg';

// ── Types ────────────────────────────────────────────────────────────

export interface BlockViewModel {
  blockId: string;
  index: number;
  type: string;
  content: string;
  // UI state (computed client-side, but stored durably)
  isInSegment: boolean;
  segmentStart: boolean;
  segmentEnd: boolean;
  bpVisible: boolean;
  suppressed: boolean;
}

export interface SnapshotEntry {
  id: string;
  conversation_id: string;
  snapshot_index: number;
  source_hash: string;
  capture_mode: string;
  block_count: number;
  created_by: string;
  created_at: string;
}

export interface BlockEntry {
  id: string;
  conversation_id: string;
  snapshot_id: string;
  block_index: number;
  parent_turn_id: string | null;
  parent_block_id: string | null;
  block_type: string;
  content_md: string;
  content_hash: string;
  // role distinguishes user vs assistant blocks; added by migration 027
  // (nexus/typescript/nebula-srv/migrations/027-block-role-column.sql).
  // Consumers (assembly-ui) group turns by this value, so it must round-trip.
  role: string | null;
  dom_path: string | null;
  dom_fingerprint: string | null;
  first_line_no: number | null;
  last_line_no: number | null;
  created_at: string;
}

export interface SegmentEntry {
  id: string;
  conversation_id: string;
  snapshot_id: string;
  start_block_id: string;
  end_block_id: string;
  start_block_index: number;
  end_block_index: number;
  segment_type: string | null;
  state: string;
  source: string;
  title: string | null;
  notes_md: string | null;
  created_by: string;
  created_at: string;
}

export interface HarvestReferenceEntry {
  id: string;
  conversation_id: string;
  snapshot_id: string;
  source_block_id: string | null;
  source_segment_id: string | null;
  target_block_id: string | null;
  target_segment_id: string | null;
  edge_type: string;
  confidence: number;
  state: string;
  source: string;
  reason: string | null;
  evidence_json: any;
  provenance_json: any;
  created_by: string;
  created_at: string;
}

export interface ProjectionOverrideEntry {
  id: string;
  conversation_id: string;
  snapshot_id: string;
  target_type: string;
  target_id: string;
  projection_target: string;
  override_type: string;
  reason_code: string;
  notes_md: string | null;
  source: string;
  created_by: string;
  created_at: string;
}

// ── 1. List snapshots for a conversation ─────────────────────────────

export async function listSnapshots(
  pool: Pool,
  conversationId: string,
): Promise<{ snapshots: SnapshotEntry[] }> {
  const { rows } = await pool.query(
    `SELECT id, conversation_id, snapshot_index, source_hash, capture_mode,
            block_count, created_by,
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
     FROM nebula.conversation_snapshots
     WHERE conversation_id = $1
     ORDER BY snapshot_index DESC`,
    [conversationId],
  );
  return { snapshots: rows };
}

// ── 2. List blocks for a snapshot, with optional diff ────────────────

export async function listBlocks(
  pool: Pool,
  snapshotId: string,
  diffFrom?: string,
): Promise<{
  blocks: BlockEntry[];
  segments: SegmentEntry[];
  overrides: ProjectionOverrideEntry[];
  diff?: { added: number; modified: number; removed: number; unchanged: number };
}> {
  const { rows: blocks } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, block_index, parent_turn_id,
            parent_block_id, block_type, content_md, content_hash,
            role,
            dom_path, dom_fingerprint, first_line_no, last_line_no,
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
     FROM nebula.conversation_blocks
     WHERE snapshot_id = $1
     ORDER BY block_index`,
    [snapshotId],
  );

  // Fetch segments and overrides for this snapshot
  const { rows: segments } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, start_block_id, end_block_id,
            start_block_index, end_block_index, segment_type, state, source,
            title, notes_md, created_by,
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
     FROM nebula.segments
     WHERE snapshot_id = $1
     ORDER BY start_block_index`,
    [snapshotId],
  );

  const { rows: overrides } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, target_type, target_id,
            projection_target, override_type, reason_code, notes_md,
            source, created_by,
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
     FROM nebula.projection_overrides
     WHERE snapshot_id = $1
     ORDER BY created_at`,
    [snapshotId],
  );

  // Diff against previous snapshot if requested
  let diff: { added: number; modified: number; removed: number; unchanged: number } | undefined;
  if (diffFrom) {
    const { rows: prevBlocks } = await pool.query(
      `SELECT block_index, content_hash
       FROM nebula.conversation_blocks
       WHERE snapshot_id = $1
       ORDER BY block_index`,
      [diffFrom],
    );

    const prevMap = new Map<number, string>();
    for (const pb of prevBlocks) prevMap.set(pb.block_index, pb.content_hash);

    diff = { added: 0, modified: 0, removed: 0, unchanged: 0 };
    const seenIndexes = new Set<number>();
    for (const b of blocks) {
      seenIndexes.add(b.block_index);
      const prevHash = prevMap.get(b.block_index);
      if (prevHash === undefined) {
        diff.added++;
      } else if (prevHash !== b.content_hash) {
        diff.modified++;
      } else {
        diff.unchanged++;
      }
    }
    // Count blocks that were removed (exist in prev but not current)
    for (const idx of prevMap.keys()) {
      if (!seenIndexes.has(idx)) diff.removed++;
    }
  }

  return { blocks, segments, overrides, diff };
}

// ── 3. Create a new snapshot ─────────────────────────────────────────

export async function createSnapshot(
  pool: Pool,
  params: {
    conversationId: string;
    snapshotIndex: number;
    sourceHash: string;
    captureMode?: string;
    blockCount?: number;
    createdBy?: string;
    blocks?: Array<{
      blockIndex: number;
      blockType: string;
      contentMd: string;
      contentHash: string;
      parentTurnId?: string;
      parentBlockId?: string;
      domPath?: string;
      domFingerprint?: string;
      firstLineNo?: number;
      lastLineNo?: number;
    }>;
  },
): Promise<{ snapshot: SnapshotEntry; blockCount: number }> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');

    // Insert snapshot
    const { rows: [snapshot] } = await client.query(
      `INSERT INTO nebula.conversation_snapshots
       (conversation_id, snapshot_index, source_hash, capture_mode,
        block_count, created_by)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING id, conversation_id, snapshot_index, source_hash,
                 capture_mode, block_count, created_by,
                 to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at`,
      [
        params.conversationId,
        params.snapshotIndex,
        params.sourceHash,
        params.captureMode || 'INCREMENTAL',
        params.blockCount || 0,
        params.createdBy || 'SYSTEM',
      ],
    );

    // Insert blocks if provided
    let insertedBlockCount = 0;
    if (params.blocks && params.blocks.length > 0) {
      for (const b of params.blocks) {
        await client.query(
          `INSERT INTO nebula.conversation_blocks
           (conversation_id, snapshot_id, block_index, parent_turn_id,
            parent_block_id, block_type, content_md, content_hash,
            dom_path, dom_fingerprint, first_line_no, last_line_no)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)`,
          [
            params.conversationId,
            snapshot.id,
            b.blockIndex,
            b.parentTurnId || null,
            b.parentBlockId || null,
            b.blockType || 'paragraph',
            b.contentMd,
            b.contentHash,
            b.domPath || null,
            b.domFingerprint || null,
            b.firstLineNo || null,
            b.lastLineNo || null,
          ],
        );
        insertedBlockCount++;
      }
    }

    await client.query('COMMIT');
    return { snapshot, blockCount: insertedBlockCount };
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

// ── 4. Commit a segment ──────────────────────────────────────────────

/**
 * Resolve block UUID from index for a given snapshot.
 * Returns null if no block is found at that index.
 */
async function resolveBlockId(
  pool: Pool,
  snapshotId: string,
  blockIndex: number,
): Promise<string | null> {
  const { rows } = await pool.query(
    `SELECT id FROM nebula.conversation_blocks
     WHERE snapshot_id = $1 AND block_index = $2
     LIMIT 1`,
    [snapshotId, blockIndex],
  );
  return rows.length > 0 ? rows[0].id : null;
}

/** Validate whether a string is a UUID‑format value. */
function looksLikeUuid(s: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
}

export async function createSegment(
  pool: Pool,
  params: {
    conversationId: string;
    snapshotId: string;
    startBlockId: string;
    endBlockId: string;
    startBlockIndex: number;
    endBlockIndex: number;
    segmentType?: string;
    source?: string;
    title?: string;
    notesMd?: string;
    createdBy?: string;
  },
): Promise<SegmentEntry> {
  // Resolve start/end block UUIDs from indices when the provided IDs
  // are not valid UUIDs (legacy client sends "block-<index>").
  let startBlockId = params.startBlockId;
  let endBlockId = params.endBlockId;

  if (!looksLikeUuid(startBlockId)) {
    const resolved = await resolveBlockId(pool, params.snapshotId, params.startBlockIndex);
    if (!resolved) {
      throw new Error(`No block found at index ${params.startBlockIndex} in snapshot ${params.snapshotId}`);
    }
    startBlockId = resolved;
  }
  if (!looksLikeUuid(endBlockId)) {
    const resolved = await resolveBlockId(pool, params.snapshotId, params.endBlockIndex);
    if (!resolved) {
      throw new Error(`No block found at index ${params.endBlockIndex} in snapshot ${params.snapshotId}`);
    }
    endBlockId = resolved;
  }

  const { rows: [segment] } = await pool.query(
    `INSERT INTO nebula.segments
     (conversation_id, snapshot_id, start_block_id, end_block_id,
      start_block_index, end_block_index, segment_type, state, source,
      title, notes_md, created_by)
     VALUES ($1, $2, $3, $4, $5, $6, $7, 'CONFIRMED', $8, $9, $10, $11)
     RETURNING id, conversation_id, snapshot_id, start_block_id,
               end_block_id, start_block_index, end_block_index,
               segment_type, state, source, title, notes_md, created_by,
               to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at`,
    [
      params.conversationId,
      params.snapshotId,
      startBlockId,
      endBlockId,
      params.startBlockIndex,
      params.endBlockIndex,
      params.segmentType || null,
      params.source || 'USER',
      params.title || null,
      params.notesMd || null,
      params.createdBy || 'USER',
    ],
  );
  return segment;
}

// ── 5. Update a segment ──────────────────────────────────────────────

export async function updateSegment(
  pool: Pool,
  segmentId: string,
  updates: {
    segmentType?: string;
    state?: string;
    title?: string;
    notesMd?: string;
  },
): Promise<SegmentEntry | null> {
  const sets: string[] = [];
  const vals: any[] = [];
  let i = 1;

  if (updates.segmentType !== undefined) { sets.push(`segment_type = $${i++}`); vals.push(updates.segmentType); }
  if (updates.state !== undefined) { sets.push(`state = $${i++}`); vals.push(updates.state); }
  if (updates.title !== undefined) { sets.push(`title = $${i++}`); vals.push(updates.title); }
  if (updates.notesMd !== undefined) { sets.push(`notes_md = $${i++}`); vals.push(updates.notesMd); }

  if (sets.length === 0) return null;

  vals.push(segmentId);
  const { rows: [segment] } = await pool.query(
    `UPDATE nebula.segments SET ${sets.join(', ')} WHERE id = $${i}
     RETURNING id, conversation_id, snapshot_id, start_block_id,
               end_block_id, start_block_index, end_block_index,
               segment_type, state, source, title, notes_md, created_by,
               to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at`,
    vals,
  );
  return segment || null;
}

// ── 6. Supersede a segment (bitemporal expire) ───────────────────────

export async function supersedeSegment(
  pool: Pool,
  segmentId: string,
): Promise<{ ok: boolean }> {
  const { rowCount } = await pool.query(
    'UPDATE nebula.segments SET valid_until = now() WHERE id = $1 AND valid_until > now()',
    [segmentId],
  );
  if (rowCount === 0) throw new Error('Segment not found');
  return { ok: true };
}

// ── 7. Add a projection override ─────────────────────────────────────

export async function createProjectionOverride(
  pool: Pool,
  params: {
    conversationId: string;
    snapshotId: string;
    targetType: string;
    targetId: string;
    projectionTarget?: string;
    overrideType?: string;
    reasonCode?: string;
    notesMd?: string;
    source?: string;
    createdBy?: string;
  },
): Promise<ProjectionOverrideEntry> {
  const { rows: [override] } = await pool.query(
    `INSERT INTO nebula.projection_overrides
     (conversation_id, snapshot_id, target_type, target_id,
      projection_target, override_type, reason_code, notes_md,
      source, created_by)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
     RETURNING id, conversation_id, snapshot_id, target_type, target_id,
               projection_target, override_type, reason_code, notes_md,
               source, created_by,
               to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at`,
    [
      params.conversationId,
      params.snapshotId,
      params.targetType || 'BLOCK',
      params.targetId,
      params.projectionTarget || 'BP',
      params.overrideType || 'EXCLUDE',
      params.reasonCode || 'USER_OVERRIDE',
      params.notesMd || null,
      params.source || 'USER',
      params.createdBy || 'USER',
    ],
  );
  return override;
}

// ── 8. Remove a projection override (bitemporal expire) ──────────────

export async function removeProjectionOverride(
  pool: Pool,
  overrideId: string,
): Promise<{ ok: boolean }> {
  const { rowCount } = await pool.query(
    'UPDATE nebula.projection_overrides SET valid_until = now() WHERE id = $1 AND valid_until > now()',
    [overrideId],
  );
  if (rowCount === 0) throw new Error('Override not found');
  return { ok: true };
}

// ── 9. Get BP projection for a snapshot ──────────────────────────────

export async function getProjection(
  pool: Pool,
  snapshotId: string,
  projectionTarget: string = 'BP',
): Promise<{
  blocks: BlockEntry[];
  segments: SegmentEntry[];
  overrides: ProjectionOverrideEntry[];
}> {
  // Get all blocks
  const { rows: blocks } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, block_index, parent_turn_id,
            parent_block_id, block_type, content_md, content_hash,
            role,
            dom_path, dom_fingerprint, first_line_no, last_line_no,
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
     FROM nebula.conversation_blocks
     WHERE snapshot_id = $1
     ORDER BY block_index`,
    [snapshotId],
  );

  // Get all segments
  const { rows: segments } = await pool.query(
    `SELECT * FROM nebula.segments
     WHERE snapshot_id = $1
     ORDER BY start_block_index`,
    [snapshotId],
  );

  // Get active overrides for this projection target
  const { rows: overrides } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, target_type, target_id,
            projection_target, override_type, reason_code, notes_md,
            source, created_by,
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
     FROM nebula.projection_overrides
     WHERE snapshot_id = $1
       AND projection_target = $2
     ORDER BY created_at`,
    [snapshotId, projectionTarget],
  );

  return { blocks, segments, overrides };
}

// ── 10. Get harvest references for a snapshot ────────────────────────

export async function listReferences(
  pool: Pool,
  snapshotId: string,
  filters?: {
    state?: string;
    edgeType?: string;
    minConfidence?: number;
  },
): Promise<{ references: HarvestReferenceEntry[] }> {
  const clauses: string[] = ['snapshot_id = $1'];
  const vals: any[] = [snapshotId];
  let i = 2;

  if (filters?.state) { clauses.push(`state = $${i++}`); vals.push(filters.state); }
  if (filters?.edgeType) { clauses.push(`edge_type = $${i++}`); vals.push(filters.edgeType); }
  if (filters?.minConfidence !== undefined) { clauses.push(`confidence >= $${i++}`); vals.push(filters.minConfidence); }

  const where = clauses.join(' AND ');

  const { rows } = await pool.query(
    `SELECT id, conversation_id, snapshot_id, source_block_id,
            source_segment_id, target_block_id, target_segment_id,
            edge_type, confidence, state, source, reason,
            evidence_json, provenance_json, created_by,
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
     FROM nebula.harvest_references
     WHERE ${where}
     ORDER BY confidence DESC`,
    vals,
  );
  return { references: rows };
}
