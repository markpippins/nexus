import { Router } from 'express';
import { pool } from '../db.js';
import { isAcceptableId, clampLimit, clampOffset } from '../lib/pagination.js';
import { badRequest, notFound } from '../errors.js';

export const entitiesRouter = Router();

// GET /api/peb/entities/{entity_id}/capability-gap
//
// Spec: "overlay every capabilities grant/expiry against every
// violations.capability_attempted" — that's the view that answers
// "was this agent trying to do something it was never supposed to, or did
// its grant just lapse at a bad time," which are very different governance
// stories.
//
// Implementation:
//   for each violation with non-null capability_attempted and entity_id,
//     LEFT JOIN to capabilities (entity_id + capability_attempted) where
//        -- the capability grant existed at the moment the violation fired:
//        c.created_at <= v.created_at
//        AND (c.expires_at IS NULL OR c.expires_at > v.created_at)
//   gap_status:
//     "active" : a live grant was in-window when the attempt fired
//     "lapsed" : a grant had existed for this capability but had expired
//                or been deactivated before the attempt
//     "missing": no capability grant ever existed for this id + capability
entitiesRouter.get('/:entity_id/capability-gap', async (req, res, next) => {
  try {
    const entity_id = req.params.entity_id;
    if (!isAcceptableId(entity_id)) return next(badRequest('invalid entity_id'));

    const limit  = clampLimit(req.query.limit);
    const offset = clampOffset(req.query.offset);

    const r = await pool.query(
      `
      SELECT v.id AS violation_id,
             v.violation_type,
             v.severity,
             v.capability_attempted,
             v.context,
             v.resolution,
             v.created_at AS violation_created_at,
             -- grants that were ACTIVE (in-window) at the moment of the
             -- violation. Returns [] when gap_status = missing / lapsed.
             (
               SELECT COALESCE(jsonb_agg(jsonb_build_object(
                  'capability_id', c.id,
                  'capability',    c.capability,
                  'granted_by',    c.granted_by,
                  'granted_at',    c.created_at,
                  'expires_at',    c.expires_at,
                  'active',        c.active
                )), '[]'::jsonb)
                 FROM peb.capabilities c
                WHERE c.entity_id = v.entity_id
                  AND c.capability = v.capability_attempted
                  AND c.created_at <= v.created_at
                  AND (c.expires_at IS NULL OR c.expires_at > v.created_at)
                  AND c.active = true
             ) AS active_grants_at_violation,
             -- grants that existed once but had expired/lapsed by the moment
             -- of the attempt. Returns [] when no such grant.
             (
               SELECT COALESCE(jsonb_agg(jsonb_build_object(
                  'capability_id', c.id,
                  'capability',    c.capability,
                  'granted_by',    c.granted_by,
                  'granted_at',     c.created_at,
                  'expires_at',     c.expires_at,
                  'active',         c.active
                )), '[]'::jsonb)
                 FROM peb.capabilities c
                WHERE c.entity_id = v.entity_id
                  AND c.capability = v.capability_attempted
                  AND NOT (
                     c.created_at <= v.created_at
                     AND (c.expires_at IS NULL OR c.expires_at > v.created_at)
                  )
             ) AS lapsed_grants_at_violation,
             CASE WHEN EXISTS (
                   SELECT 1 FROM peb.capabilities c
                   WHERE c.entity_id = v.entity_id
                     AND c.capability = v.capability_attempted
                     AND c.created_at <= v.created_at
                     AND (c.expires_at IS NULL OR c.expires_at > v.created_at)
                     AND c.active = true
             ) THEN 'active'
                  WHEN EXISTS (
                   SELECT 1 FROM peb.capabilities c
                   WHERE c.entity_id = v.entity_id
                     AND c.capability = v.capability_attempted
                  ) THEN 'lapsed'
                  ELSE 'missing'
             END AS gap_status
        FROM peb.violations v
       WHERE v.entity_id = $1
         AND v.capability_attempted IS NOT NULL
       ORDER BY v.created_at DESC
       LIMIT $2 OFFSET $3
      `,
      [entity_id, limit, offset]
    );

    const summary = r.rows.reduce((acc, row) => {
      acc[row.gap_status] = (acc[row.gap_status] ?? 0) + 1;
      return acc;
    }, { active: 0, lapsed: 0, missing: 0 });

    res.json({ entity_id, capability_gaps: r.rows, summary });
  } catch (err) {
    next(err);
  }
});

// Convenience: capabilities for an entity (list, with a status rollup)
// Not in the spec but useful for the UI to ground the gap view.
entitiesRouter.get('/:entity_id/capabilities', async (req, res, next) => {
  try {
    const entity_id = req.params.entity_id;
    if (!isAcceptableId(entity_id)) return next(badRequest('invalid entity_id'));
    const r = await pool.query(
      `SELECT c.id, c.entity_id, c.capability, c.granted_by, c.expires_at,
              c.active, c.created_at,
              CASE WHEN c.expires_at IS NOT NULL AND c.expires_at < now()
                   THEN 'expired'
                   ELSE 'active'
              END AS status
         FROM peb.capabilities c
        WHERE c.entity_id = $1
        ORDER BY c.created_at DESC`,
      [entity_id]
    );
    res.json({ entity_id, capabilities: r.rows });
  } catch (err) {
    next(err);
  }
});
