import { Router } from 'express';
import { pool } from '../db.js';
import { isAcceptableId } from '../lib/pagination.js';
import { badRequest, notFound } from '../errors.js';

export const transactionsRouter = Router();

// GET /api/peb/transactions?entity_id=&tool_name=&admission_result=&since=&limit=&offset=
transactionsRouter.get('/', async (req, res, next) => {
  try {
    const limit  = Math.min(500, Math.max(1,  parseInt(req.query.limit  ?? '100', 10) || 100));
    const offset = Math.max(0, parseInt(req.query.offset ?? '0', 10) || 0);
    const args = [limit, offset];
    const where = [];
    let n = 3;
    for (const [field, value] of Object.entries({
      entity_id:        req.query.entity_id,
      tool_name:        req.query.tool_name,
      admission_result: req.query.admission_result,
    })) {
      if (value != null && value !== '') {
        where.push(`t.${field} = $${n++}`);
        args.push(String(value));
      }
    }
    if (req.query.since) {
      where.push(`t.created_at >= $${n++}`);
      args.push(new Date(req.query.since));
    }
    const q = `
      SELECT id, idempotency_key, entity_id, admission_result, tool_name,
             input, output, before_hash, after_hash, state_delta,
             created_at, committed_at, kernel_event_id, kernel_event_type
      FROM peb.transactions t
      ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
      ORDER BY t.created_at DESC
      LIMIT $1 OFFSET $2
    `;
    const r = await pool.query(q, args);
    res.json({ transactions: r.rows });
  } catch (err) {
    next(err);
  }
});

// GET /api/peb/transactions/{id}
transactionsRouter.get('/:id', async (req, res, next) => {
  try {
    const id = req.params.id;
    if (!isAcceptableId(id)) return next(badRequest('invalid id'));
    const r = await pool.query(
      `SELECT id, idempotency_key, entity_id, admission_result, tool_name,
              input, output, before_hash, after_hash, state_delta,
              created_at, committed_at, kernel_event_id, kernel_event_type
       FROM peb.transactions WHERE id = $1`,
      [id]
    );
    if (r.rowCount === 0) return next(notFound('transaction not found'));
    res.json({ transaction: r.rows[0] });
  } catch (err) {
    next(err);
  }
});

// GET /api/peb/transactions/{id}/lineage
//
// Spec: walk and return in one payload ---
//   decisions row(s) tied to it, plus parent_decision_id ancestry + rollback_of chain
//   traces tree rooted at this transaction (via parent_trace_id),
//     each node carrying confidence and rejected_alternatives
//   any violations raised, joined against the capabilities the entity_id
//     actually held at created_at (as-of join)
//   governance_events with matching work_request_id/plan_id, ordered by created_at,
//     with replayed_at surfaced
transactionsRouter.get('/:id/lineage', async (req, res, next) => {
  try {
    const id = req.params.id;
    if (!isAcceptableId(id)) return next(badRequest('invalid id'));

    const tx = await pool.query(
      `SELECT id, idempotency_key, entity_id, admission_result, tool_name,
              input, output, before_hash, after_hash, state_delta,
              created_at, committed_at, kernel_event_id, kernel_event_type
       FROM peb.transactions WHERE id = $1`,
      [id]
    );
    if (tx.rowCount === 0) return next(notFound('transaction not found'));
    const transaction = tx.rows[0];

    // Decisions tied to this transaction (single hop)
    const decisionsDirect = await pool.query(
      `SELECT * FROM peb.decisions d WHERE d.transaction_id = $1`,
      [id]
    );

    // Build the ancestry / rollback chain from each directly-tied decision.
    // Recursive CTE walking parent_decision_id and rollback_of up the chain.
    const ancestryRows = decisionsDirect.rows.length
      ? (await pool.query(
          `
          WITH RECURSIVE chain AS (
            SELECT d.id, d.parent_decision_id, d.rollback_of, d.title,
                   d.status, d.summary, d.entropy_class, d.created_at,
                   0 AS depth, 'direct' AS link
              FROM peb.decisions d
             WHERE d.transaction_id = $1
            UNION ALL
            SELECT p.id, p.parent_decision_id, p.rollback_of, p.title,
                   p.status, p.summary, p.entropy_class, p.created_at,
                   ch.depth + 1 AS depth,
                   CASE WHEN ch.rollback_of = p.id THEN 'rollback_of'
                        WHEN ch.parent_decision_id = p.id THEN 'parent'
                   END AS link
              FROM peb.decisions p
              JOIN chain ch
                ON ch.parent_decision_id = p.id OR ch.rollback_of = p.id
             WHERE ch.depth < 50
          )
          SELECT DISTINCT ON (chain.id) chain.* FROM chain ORDER BY chain.id, chain.depth
          `,
          [id]
        )).rows
      : [];

    // Traces rooted at this transaction; we return the flat set and let the
    // client build the parent_trace_id tree (we also build a shaped tree here).
    const tracesRows = (await pool.query(
      `SELECT id, transaction_id, work_request_id, parent_trace_id, stage,
              inputs, causal_entries, rejected_alternatives, confidence,
              status, created_at
       FROM peb.traces WHERE transaction_id = $1`,
      [id]
    )).rows;

    const tracesTree = buildTraceTree(tracesRows);

    // Violations: feature the as-of capabilities overlay via LATERAL subquery
    // (see capability-gap route for the more complete version; here we
    // surface the same overlay inline for the lineage dump).
    const violations = (await pool.query(
      `
      SELECT v.id, v.violation_type, v.severity, v.capability_attempted,
             v.context, v.resolution, v.created_at,
             COALESCE(
                (SELECT jsonb_agg(jsonb_build_object(
                          'capability_id', c.id,
                          'capability', c.capability,
                          'active', c.active,
                          'granted_by', c.granted_by,
                          'expires_at', c.expires_at,
                          'granted_at', c.created_at
                        ))
                   FROM peb.capabilities c
                  WHERE c.entity_id = v.entity_id
                    AND c.capability = v.capability_attempted
                    AND c.created_at <= v.created_at
                    AND (c.expires_at IS NULL OR c.expires_at > v.created_at)
                ),
                '[]'::jsonb
             ) AS capability_grants_at_violation,
             CASE WHEN EXISTS (
                   SELECT 1 FROM peb.capabilities c2
                   WHERE c2.entity_id = v.entity_id
                     AND c2.capability = v.capability_attempted
                     AND c2.created_at <= v.created_at
                     AND (c2.expires_at IS NULL OR c2.expires_at > v.created_at)
             ) THEN false ELSE true END AS gap_detected
        FROM peb.violations v
       WHERE v.transaction_id = $1
       ORDER BY v.created_at
      `,
      [id]
    )).rows;

    // Governance events: matching work_request_id OR plan_id.
    // We don't have direct work_request_id on tx; we use traces.work_request_id
    // or governance_events.plan_id (which we cannot derive without a tie --
    // so we fall back to: any plan_id referenced by any trace tied to this tx).
    let govEvents = [];
    if (tracesRows.length > 0) {
      const wrIds = Array.from(new Set(
        tracesRows.map(t => t.work_request_id).filter(Boolean)
      ));
      if (wrIds.length > 0) {
        const govRes = await pool.query(
          `SELECT ge.id, ge.receipt_id, ge.event_type, ge.work_request_id,
                  ge.plan_id, ge.agent_role, ge.payload, ge.created_at, ge.replayed_at
             FROM peb.governance_events ge
            WHERE ge.work_request_id = ANY($1::text[])
            ORDER BY ge.created_at ASC`,
          [wrIds]
        );
        govEvents = govRes.rows;
      }
    }

    res.json({
      transaction,
      decisions: decisionsDirect.rows,
      decision_chain: ancestryRows,
      traces: tracesRows,
      traces_tree: tracesTree,
      violations,
      governance_events: govEvents,
    });
  } catch (err) {
    next(err);
  }
});

// Convert a flat list of traces (which may include parent_trace_id pointers
// into other transactions' traces) into a tree keyed by trace.id. We only
// include traces that pass through THIS transaction here; tree edges come
// from parent_trace_id when the parent trace is also in the same set.
function buildTraceTree(rows) {
  const byId = new Map(rows.map(r => [r.id, { ...r, children: [] }]));
  const roots = [];
  for (const r of byId.values()) {
    if (r.parent_trace_id && byId.has(r.parent_trace_id)) {
      byId.get(r.parent_trace_id).children.push(r);
    } else {
      roots.push(r);
    }
  }
  return roots;
}
