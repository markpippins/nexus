import { Router } from 'express';
import { pool } from '../db.js';
import { isAcceptableId } from '../lib/pagination.js';
import { badRequest, notFound } from '../errors.js';

export const tracesRouter = Router();

// GET /api/peb/traces/{id}/tree
//
// Spec: includes rejected_alternatives at each node.
//
// A trace lives in `peb.traces` and may have a `parent_trace_id` pointing at
// another trace (could be in a different transaction). We return the full
// subtree rooted at `id` (including all descendants across transactions),
// followed by the chain of ancestors from `id` up to the root (so callers can
// render the full path).
tracesRouter.get('/:id/tree', async (req, res, next) => {
  try {
    const id = req.params.id;
    if (!isAcceptableId(id)) return next(badRequest('invalid id'));

    const head = await pool.query(
      `SELECT id FROM peb.traces WHERE id = $1::uuid`, [id]
    );
    if (head.rowCount === 0) return next(notFound('trace not found'));

    // Descendants via recursive parent_trace_id.
    const descendants = await pool.query(
      `
      WITH RECURSIVE walk AS (
        SELECT id, 0 AS depth
          FROM peb.traces WHERE id = $1::uuid
        UNION ALL
        SELECT t.id, w.depth + 1
          FROM peb.traces t
          JOIN walk w ON t.parent_trace_id = w.id
         WHERE w.depth < 200
      )
      SELECT t.id, t.transaction_id, t.work_request_id, t.parent_trace_id,
             t.stage, t.inputs, t.causal_entries, t.rejected_alternatives,
             t.confidence, t.status, t.created_at, w.depth
        FROM peb.traces t
        JOIN walk w ON t.id = w.id
       ORDER BY w.depth, t.created_at
      `,
      [id]
    );
    const flat = descendants.rows;
    const tree = buildTree(flat);

    res.json({ root_id: id, node_count: flat.length, tree });
  } catch (err) {
    next(err);
  }
});

// Build a parent-pointer-tree from a flat list of rows, each carrying
// `id`, `parent_trace_id`, and `depth`.
function buildTree(rows) {
  const byId = new Map(rows.map(r => [r.id, { ...r, children: [] }]));
  const roots = [];
  for (const r of byId.values()) {
    if (r.parent_trace_id && byId.has(r.parent_trace_id)) {
      byId.get(r.parent_trace_id).children.push(r);
    } else if (r.depth === 0) {
      roots.push(r);
    }
  }
  return roots;
}
