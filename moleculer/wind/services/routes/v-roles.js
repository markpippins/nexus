import { Router } from 'express';
import { query } from '../db.js';

export const vRolesRouter = Router();

// List roles (from nebula.roles via wind.v_roles view)
vRolesRouter.get('/', async (_req, res, next) => {
  try {
    const result = await query(
      'SELECT id, name, display_name, description, owns_domains, can_greenlight, can_create_questions, can_create_agendas, can_resolve_questions, can_verify_work_requests, max_open_questions, requires_approval_from, cron_enabled, cron_expression, cron_description, escalates_to, escalation_triggers, level_filter_primary, level_filter_allowed, visibility_scope, created_at, updated_at FROM wind.v_roles ORDER BY name'
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get role by name
vRolesRouter.get('/:name', async (req, res, next) => {
  try {
    const result = await query(
      'SELECT id, name, display_name, description, owns_domains, can_greenlight, can_create_questions, can_create_agendas, can_resolve_questions, can_verify_work_requests, max_open_questions, requires_approval_from, cron_enabled, cron_expression, cron_description, escalates_to, escalation_triggers, level_filter_primary, level_filter_allowed, visibility_scope, created_at, updated_at FROM wind.v_roles WHERE name = $1',
      [req.params.name]
    );
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Role not found' });
    }
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});
