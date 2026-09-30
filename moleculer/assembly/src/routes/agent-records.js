import { Router } from 'express';
import { NotFoundError } from '../errors.js';
import { fetchNebula, snakeToCamel } from '../utils/fetchNebula.js';

export const agentRecordsRouter = Router();

/**
 * Proxy agent records to nebula-srv using fetch helper.
 * Forwards all supported query params (type, role, systemId, subsystemId,
 * featureId, planRef, tag, search, createdAfter, createdBefore, level,
 * visibilityScope, page, pageSize) to nebula-srv.
 * Maintains API contract with snake_case to camelCase conversion for frontend.
 */
agentRecordsRouter.get('/', async (req, res, next) => {
  try {
    // Forward all query params to nebula-srv (it supports type, role, tag, etc.)
    const nebulaResponse = await fetchNebula('/agent-records', {
      ...req.query,
      page: req.query.page || '1',
      pageSize: req.query.pageSize || '100',
    });

    // Convert snake_case to camelCase for frontend compatibility
    if (nebulaResponse.items) {
      nebulaResponse.items = nebulaResponse.items.map(snakeToCamel);
    }

    res.json(nebulaResponse);
  } catch (err) {
    next(err);
  }
});

agentRecordsRouter.get('/:id', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula(`/agent-records/${req.params.id}`);
    if (!nebulaResponse || !nebulaResponse.id) {
      throw new NotFoundError('Not found');
    }

    // Convert snake_case to camelCase for frontend compatibility
    const camelCaseRecord = snakeToCamel(nebulaResponse);
    res.json(camelCaseRecord);
  } catch (err) {
    next(err);
  }
});
