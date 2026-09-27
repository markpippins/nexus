import { Router } from 'express';
import { pool } from '../db.js';
import { isAcceptableId } from '../lib/pagination.js';
import { badRequest, notFound, conflict } from '../errors.js';

export const decisionsRouter = Router();

// ── List decisions ─────────────────────────────────────────────────
// GET /api/peb/decisions?status=&author_id=&adr_number=&affected_key=&limit=&offset=
decisionsRouter.get('/', async (req, res, next) => {
  try {
    const limit  = Math.min(500, Math.max(1, parseInt(req.query.limit ?? '100', 10) || 100));
    const offset = Math.max(0, parseInt(req.query.offset ?? '0', 10) || 0);
    const args = [limit, offset];
    const where = [];
    let n = 3;

    for (const [field, value] of Object.entries({
      status:     req.query.status,
      author_id:  req.query.author_id,
      adr_number: req.query.adr_number,
    })) {
      if (value != null && value !== '') {
        where.push(`d.${field} = $${n++}`);
        args.push(String(value));
      }
    }

    // Filter by affected key membership (array overlap).
    if (req.query.affected_key) {
      where.push(`d.affected_keys && $${n++}`);
      args.push([req.query.affected_key]);
    }

    const q = `
      SELECT id, adr_number, title, status, summary, affected_keys,
             entropy_class, author_id, parent_decision_id, rollback_of,
             created_at
      FROM peb.decisions d
      ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
      ORDER BY d.created_at DESC
      LIMIT $1 OFFSET $2
    `;
    const r = await pool.query(q, args);
    res.json({ decisions: r.rows, limit, offset });
  } catch (err) { next(err); }
});

// ── Get next ADR number ───────────────────────────────────────────
// GET /api/peb/decisions/next-number
decisionsRouter.get('/next-number', async (_req, res, next) => {
  try {
    const r = await pool.query(
      `SELECT adr_number FROM peb.decisions
       WHERE adr_number IS NOT NULL
       ORDER BY (regexp_replace(adr_number, '[^0-9]', '', 'g'))::int DESC
       LIMIT 1`
    );
    const last = r.rows[0]?.adr_number;
    const lastNum = last ? parseInt(last.replace(/\D/g, ''), 10) || 0 : 0;
    res.json({ next: `ADR-${String(lastNum + 1).padStart(3, '0')}`, last });
  } catch (err) { next(err); }
});

// ── Get decision by ID ────────────────────────────────────────────
// GET /api/peb/decisions/:id
decisionsRouter.get('/:id', async (req, res, next) => {
  try {
    const id = req.params.id;
    if (!isAcceptableId(id)) return next(badRequest('invalid id'));
    const r = await pool.query(
      `SELECT * FROM peb.decisions WHERE id = $1::uuid`, [id]
    );
    if (r.rowCount === 0) return next(notFound('decision not found'));
    res.json(r.rows[0]);
  } catch (err) { next(err); }
});

// ── Create decision (ADR) ─────────────────────────────────────────
// POST /api/peb/decisions
// Body: { title, author_id, summary?, affected_keys?, entropy_class?,
//         parent_decision_id?, rollback_of?, adr_number?, status?, transaction_id? }
decisionsRouter.post('/', async (req, res, next) => {
  try {
    const {
      title, author_id, summary, affected_keys, entropy_class,
      parent_decision_id, rollback_of, adr_number, status, transaction_id,
    } = req.body;

    if (!title) return next(badRequest('title is required'));
    if (!author_id) return next(badRequest('author_id is required'));

    // Auto-assign ADR number if not provided.
    let finalNumber = adr_number || null;
    if (!finalNumber) {
      const r = await pool.query(
        `SELECT adr_number FROM peb.decisions
         WHERE adr_number IS NOT NULL
         ORDER BY (regexp_replace(adr_number, '[^0-9]', '', 'g'))::int DESC
         LIMIT 1`
      );
      const last = r.rows[0]?.adr_number;
      const lastNum = last ? parseInt(last.replace(/\D/g, ''), 10) || 0 : 0;
      finalNumber = `ADR-${String(lastNum + 1).padStart(3, '0')}`;
    }

    // Hash the summary for change tracking.
    const crypto = await import('crypto');
    const afterHash = summary
      ? crypto.createHash('sha256').update(JSON.stringify(summary)).digest('hex')
      : null;

    const r = await pool.query(
      `INSERT INTO peb.decisions
         (id, transaction_id, adr_number, title, status, summary,
          affected_keys, entropy_class, before_hash, after_hash,
          author_id, parent_decision_id, rollback_of, created_at)
       VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, NULL, $8, $9, $10, $11, now())
       RETURNING *`,
      [
        transaction_id || '00000000-0000-0000-0000-000000000000',
        finalNumber,
        title,
        status || 'proposed',
        summary ? JSON.stringify(summary) : null,
        affected_keys || null,
        entropy_class || null,
        afterHash,
        author_id,
        parent_decision_id || null,
        rollback_of || null,
      ]
    );

    res.status(201).json(r.rows[0]);
  } catch (err) { next(err); }
});

// ── Update decision ───────────────────────────────────────────────
// PATCH /api/peb/decisions/:id
// Body: { title?, summary?, status?, affected_keys?, entropy_class?, parent_decision_id? }
decisionsRouter.patch('/:id', async (req, res, next) => {
  try {
    const id = req.params.id;
    if (!isAcceptableId(id)) return next(badRequest('invalid id'));

    // Verify exists and fetch current state for before_hash.
    const existing = await pool.query(
      `SELECT * FROM peb.decisions WHERE id = $1::uuid`, [id]
    );
    if (existing.rowCount === 0) return next(notFound('decision not found'));

    const current = existing.rows[0];
    const updates = [];
    const args = [];
    let n = 1;

    for (const field of ['title', 'status', 'entropy_class', 'parent_decision_id']) {
      if (req.body[field] !== undefined) {
        updates.push(`${field} = $${n++}`);
        args.push(req.body[field]);
      }
    }
    if (req.body.affected_keys !== undefined) {
      updates.push(`affected_keys = $${n++}`);
      args.push(req.body.affected_keys);
    }
    if (req.body.summary !== undefined) {
      updates.push(`summary = $${n++}`);
      args.push(JSON.stringify(req.body.summary));
      // Recompute after_hash on summary change.
      const crypto = await import('crypto');
      const afterHash = crypto.createHash('sha256').update(JSON.stringify(req.body.summary)).digest('hex');
      updates.push(`before_hash = $${n++}`);
      args.push(current.after_hash);
      updates.push(`after_hash = $${n++}`);
      args.push(afterHash);
    }

    if (updates.length === 0) {
      return res.json(current);
    }

    args.push(id);
    const r = await pool.query(
      `UPDATE peb.decisions SET ${updates.join(', ')} WHERE id = $${n} RETURNING *`,
      args
    );
    res.json(r.rows[0]);
  } catch (err) { next(err); }
});

// ── Supersede a decision ──────────────────────────────────────────
// POST /api/peb/decisions/:id/supersede
// Body: { summary, author_id, affected_keys? }
// Creates a new decision that supersedes this one.
decisionsRouter.post('/:id/supersede', async (req, res, next) => {
  try {
    const id = req.params.id;
    if (!isAcceptableId(id)) return next(badRequest('invalid id'));

    const existing = await pool.query(
      `SELECT * FROM peb.decisions WHERE id = $1::uuid`, [id]
    );
    if (existing.rowCount === 0) return next(notFound('decision not found'));

    const current = existing.rows[0];
    const { summary, author_id, title, affected_keys } = req.body;
    if (!summary) return next(badRequest('summary is required'));
    if (!author_id) return next(badRequest('author_id is required'));

    // Auto-assign next ADR number.
    const numR = await pool.query(
      `SELECT adr_number FROM peb.decisions
       WHERE adr_number IS NOT NULL
       ORDER BY (regexp_replace(adr_number, '[^0-9]', '', 'g'))::int DESC
       LIMIT 1`
    );
    const last = numR.rows[0]?.adr_number;
    const lastNum = last ? parseInt(last.replace(/\D/g, ''), 10) || 0 : 0;
    const newNumber = `ADR-${String(lastNum + 1).padStart(3, '0')}`;

    const crypto = await import('crypto');
    const afterHash = crypto.createHash('sha256').update(JSON.stringify(summary)).digest('hex');

    // Create new decision as superseding.
    const newDecision = await pool.query(
      `INSERT INTO peb.decisions
         (id, transaction_id, adr_number, title, status, summary,
          affected_keys, entropy_class, before_hash, after_hash,
          author_id, parent_decision_id, rollback_of, created_at)
       VALUES (gen_random_uuid(), $1, $2, $3, 'accepted', $4, $5,
               NULL, $6, $7, $8, NULL, NULL, now())
       RETURNING *`,
      [
        current.transaction_id,
        newNumber,
        title || `${current.title} (supersedes ${current.adr_number})`,
        JSON.stringify(summary),
        affected_keys || current.affected_keys,
        current.after_hash,
        afterHash,
        author_id,
      ]
    );

    // Mark the old decision as superseded.
    await pool.query(
      `UPDATE peb.decisions SET status = 'superseded' WHERE id = $1::uuid`,
      [id]
    );

    res.status(201).json({
      superseded: { id: current.id, adr_number: current.adr_number },
      decision: newDecision.rows[0],
    });
  } catch (err) { next(err); }
});

// ── Chain traversal ────────────────────────────────────────────────
// GET /api/peb/decisions/:id/chain?direction=ancestry|rollback
decisionsRouter.get('/:id/chain', async (req, res, next) => {
  try {
    const id = req.params.id;
    if (!isAcceptableId(id)) return next(badRequest('invalid id'));
    const direction = String(req.query.direction ?? 'ancestry').toLowerCase();

    const linkCol = direction === 'rollback' ? 'rollback_of' : 'parent_decision_id';

    // Confirm the decision exists.
    const head = await pool.query(
      `SELECT id FROM peb.decisions WHERE id = $1::uuid`, [id]
    );
    if (head.rowCount === 0) return next(notFound('decision not found'));

    const chain = await pool.query(
      `
      WITH RECURSIVE walk AS (
        SELECT d.id, d.transaction_id, d.adr_number, d.title, d.status,
               d.summary, d.affected_keys, d.entropy_class, d.before_hash,
               d.after_hash, d.author_id, d.parent_decision_id, d.rollback_of,
               d.created_at, 0 AS depth
          FROM peb.decisions d
         WHERE d.id = $1::uuid
        UNION ALL
        SELECT p.id, p.transaction_id, p.adr_number, p.title, p.status,
               p.summary, p.affected_keys, p.entropy_class, p.before_hash,
               p.after_hash, p.author_id, p.parent_decision_id, p.rollback_of,
               p.created_at, w.depth + 1
          FROM peb.decisions p
          JOIN walk w ON p.id = w.${linkCol}
         WHERE w.depth < 50
      )
      SELECT * FROM walk ORDER BY depth ASC
      `,
      [id]
    );

    res.json({ direction, chain: chain.rows });
  } catch (err) {
    next(err);
  }
});
