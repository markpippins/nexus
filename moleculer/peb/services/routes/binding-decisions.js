import { Router } from 'express';
import { pool } from '../db.js';

export const bindingDecisionsRouter = Router();

// GET /api/peb/binding-decisions/authority/:decisionClass
//
// Read-only authority-mode consult for the admission boundary (G1
// activation). Returns the authority level carried by the durable
// peb.state row `binding_authority_mode` (migration V135) for the
// requested decision class. Fail-safe contract (mirrors peb-kernel
// binding_authority.py): no row, malformed content, a different class,
// or any DB error resolves to `advisory` — a broken consult can never
// widen authority. No mutation surface exists on this route.
bindingDecisionsRouter.get('/authority/:decisionClass', async (req, res) => {
  const decisionClass = String(req.params.decisionClass ?? '');
  const BINDING_DECISION_CLASS = 'deny_contract_promotion';
  const NARROWLY_BINDING = 'narrowly_binding';
  const failSafe = (reason) =>
    res.json({ decision_class: decisionClass, authority_level: 'advisory', state_version: null, reason });
  try {
    const { rows } = await pool.query(
      `SELECT content, version FROM peb.state WHERE key = 'binding_authority_mode'`,
    );
    if (rows.length === 0) return failSafe('no_state_row');
    const content = rows[0].content ?? {};
    if (content.decision_class !== decisionClass) return failSafe('class_not_elevated');
    if (content.authority_level === NARROWLY_BINDING && decisionClass === BINDING_DECISION_CLASS) {
      return res.json({
        decision_class: decisionClass,
        authority_level: NARROWLY_BINDING,
        state_version: rows[0].version,
        reason: 'state_row',
      });
    }
    return failSafe('advisory_mode');
  } catch (err) {
    return failSafe(`state_lookup_error:${err?.code ?? err?.message ?? 'unknown'}`);
  }
});

// GET /api/peb/binding-decisions?subject_id=&disposition=&decision_class=&limit=&offset=
bindingDecisionsRouter.get('/', async (req, res, next) => {
  try {
    const limit = Math.min(500, Math.max(1, parseInt(req.query.limit ?? '100', 10) || 100));
    const offset = Math.max(0, parseInt(req.query.offset ?? '0', 10) || 0);
    const args = [limit, offset];
    const where = [];
    let n = 3;
    for (const field of ['subject_id', 'disposition', 'decision_class']) {
      const value = req.query[field];
      if (value != null && value !== '') {
        where.push(`b.${field} = $${n++}`);
        args.push(String(value));
      }
    }
    const result = await pool.query(
      `SELECT id, decision_id, decision_class, binding_contract_version,
              subject_id, work_item_id, disposition, authority_level,
              evaluation_fingerprint, lineage_fingerprint, replay_context,
              as_of, payload, created_at
         FROM peb.binding_decision_evidence b
        ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
        ORDER BY b.created_at DESC
        LIMIT $1 OFFSET $2`,
      args,
    );
    res.json({ decisions: result.rows, limit, offset });
  } catch (err) {
    next(err);
  }
});
