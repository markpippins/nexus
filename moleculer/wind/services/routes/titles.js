import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const titlesRouter = Router();

// List titles (optionally filter by office)
titlesRouter.get('/', async (req, res, next) => {
  try {
    const { office_id } = req.query;
    let sql = `
      SELECT t.id, t.office_id, t.role_id, t.display_name, t.created_at,
             o.name AS office_name, r.name AS role_name
      FROM wind.titles t
      JOIN wind.offices o ON t.office_id = o.id
      JOIN nebula.roles r ON t.role_id = r.id
    `;
    const params = [];
    if (office_id) {
      sql += ' WHERE t.office_id = $1';
      params.push(office_id);
    }
    sql += ' ORDER BY o.name, t.display_name';
    const result = await query(sql, params);
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get title by ID
titlesRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await query(`
      SELECT t.id, t.office_id, t.role_id, t.display_name, t.created_at,
             o.name AS office_name, r.name AS role_name
      FROM wind.titles t
      JOIN wind.offices o ON t.office_id = o.id
      JOIN nebula.roles r ON t.role_id = r.id
      WHERE t.id = $1
    `, [req.params.id]);
    if (result.rows.length === 0) throw new NotFoundError('Title not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Create title
titlesRouter.post('/', async (req, res, next) => {
  try {
    const { office_id, role_id, display_name } = req.body;
    if (!office_id || !role_id || !display_name) {
      throw new BadRequestError('office_id, role_id, and display_name are required');
    }
    // Verify office exists
    const office = await query('SELECT id FROM wind.offices WHERE id = $1', [office_id]);
    if (office.rows.length === 0) throw new NotFoundError('Office not found');
    // Verify role exists
    const role = await query('SELECT id FROM nebula.roles WHERE id = $1', [role_id]);
    if (role.rows.length === 0) throw new NotFoundError('Role not found');

    const result = await query(
      'INSERT INTO wind.titles (office_id, role_id, display_name) VALUES ($1, $2, $3) RETURNING id, office_id, role_id, display_name, created_at',
      [office_id, role_id, display_name]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

// Update title
titlesRouter.put('/:id', async (req, res, next) => {
  try {
    const { role_id, display_name } = req.body;
    const sets = [];
    const params = [];
    let idx = 1;
    if (role_id !== undefined) { sets.push(`role_id = $${idx++}`); params.push(role_id); }
    if (display_name !== undefined) { sets.push(`display_name = $${idx++}`); params.push(display_name); }
    if (sets.length === 0) {
      const r = await query('SELECT id, office_id, role_id, display_name, created_at FROM wind.titles WHERE id = $1', [req.params.id]);
      if (r.rows.length === 0) throw new NotFoundError('Title not found');
      return res.json(r.rows[0]);
    }
    params.push(req.params.id);
    const result = await query(
      `UPDATE wind.titles SET ${sets.join(', ')} WHERE id = $${idx} RETURNING id, office_id, role_id, display_name, created_at`,
      params
    );
    if (result.rows.length === 0) throw new NotFoundError('Title not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Delete title
titlesRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await query('DELETE FROM wind.titles WHERE id = $1 RETURNING id, display_name', [req.params.id]);
    if (result.rows.length === 0) throw new NotFoundError('Title not found');
    res.json({ deleted: true, id: result.rows[0].id, display_name: result.rows[0].display_name });
  } catch (err) { next(err); }
});
