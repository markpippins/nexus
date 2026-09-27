// src/routes/segment-sets.js — Read-only segment-set evidence surface for
// assembly-srv, proxied to substance (:3115).
//
// Substance owns the segment-set scheme and its cache; assembly only
// deliberates over it. See src/substance-proxy.js for the read-only rationale.
//
// MOUNTING (routes/index.js): this router is mounted ONCE at the router root
// (`routes.use('/', segmentSetsRouter)`), and every path here is therefore
// FULL — including the `/segment-sets` prefix on the list/get-by-id routes.
// Domain-evidence routes are declared BEFORE `GET /segment-sets/:id` so the
// concrete `/:domain/:id/segment-sets` paths always win over the generic
// single-segment `/:id` (Express matches in registration order; a URL like
// `/segment-sets/<uuid>` is two segments and cannot match a one-segment
// `/:domain/:id/...` route, but a bare `/segment-sets` list URL WOULD be
// captured by `/:id` if that were registered first — hence this ordering).
//
// Routes (all GET):
//   GET /segment-sets                     → list via substance limit/offset
//   GET /segment-sets/:id                 → resolved set (members + source segments)
//   GET /candidates/:id/segment-sets      → evidence linked to a harvest candidate
//   GET /requirements/:id/segment-sets    → evidence linked to a requirement
//   GET /intent-records/:id/segment-sets  → evidence linked to an intent record

import { Router } from 'express';
import { fetchSubstance, substanceToCamel } from '../substance-proxy.js';

export const segmentSetsRouter = Router();

const DOMAIN_TYPES = ['candidates', 'requirements', 'intent-records'];

/** Shared handler for the three domain-evidence routes (unrolled below —
 *  tools/api-docs/extract_routes.py parses one route per line, so the
 *  registrations must be literal, not loop-generated). */
async function domainSegmentSets(req, res, next) {
  try {
    const data = await fetchSubstance(
      `/${req.params.domainType}/${req.params.id}/segment-sets`,
    );
    res.json(substanceToCamel(data));
  } catch (err) {
    next(err);
  }
}

// eslint-disable-next-line max-statements-per-line -- one line per route for extract_routes.py
segmentSetsRouter.get('/candidates/:id/segment-sets', (req, res, next) => { req.params.domainType = 'candidates'; return domainSegmentSets(req, res, next); });
// eslint-disable-next-line max-statements-per-line
segmentSetsRouter.get('/requirements/:id/segment-sets', (req, res, next) => { req.params.domainType = 'requirements'; return domainSegmentSets(req, res, next); });
// eslint-disable-next-line max-statements-per-line
segmentSetsRouter.get('/intent-records/:id/segment-sets', (req, res, next) => { req.params.domainType = 'intent-records'; return domainSegmentSets(req, res, next); });

void DOMAIN_TYPES;

segmentSetsRouter.get('/segment-sets', async (req, res, next) => {
  try {
    const limit = Math.min(parseInt(String(req.query.limit || '200'), 10) || 200, 1000);
    const offset = Math.max(parseInt(String(req.query.offset || '0'), 10) || 0, 0);
    const data = await fetchSubstance(`/segment-sets?limit=${limit}&offset=${offset}`);
    res.json({ items: substanceToCamel(data), total: Array.isArray(data) ? data.length : 0, limit, offset });
  } catch (err) {
    next(err);
  }
});

segmentSetsRouter.get('/segment-sets/:id', async (req, res, next) => {
  try {
    const data = await fetchSubstance(`/segment-sets/${req.params.id}`);
    res.json(substanceToCamel(data));
  } catch (err) {
    next(err);
  }
});
