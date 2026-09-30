import { Router } from 'express';
import { NotFoundError } from '../errors.js';
import { fetchNebula, snakeToCamel } from '../utils/fetchNebula.js';

export const agendasRouter = Router();

/**
 * Proxy agendas to nebula-srv using fetch helper.
 * Maintains API contract with snake_case to camelCase conversion for frontend compatibility
 */
agendasRouter.get('/', async (req, res, next) => {
  try {
    const queryParams = {
      page: req.query.page || '1',
      pageSize: req.query.pageSize || '100',
    };
    
    // Forward query params to nebula-srv
    const nebulaResponse = await fetchNebula('/agendas', queryParams);

    // Convert snake_case to camelCase for frontend compatibility
    if (nebulaResponse.items) {
      nebulaResponse.items = nebulaResponse.items.map(snakeToCamel);
    }
    
    res.json(nebulaResponse);
  } catch (err) {
    next(err);
  }
});

agendasRouter.get('/:id', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula(`/agendas/${req.params.id}`);
    if (!nebulaResponse || !nebulaResponse.id) {
      throw new NotFoundError('Not found');
    }
    
    // Convert entire response to camelCase
    const camelCaseResponse = snakeToCamel(nebulaResponse);
    res.json(camelCaseResponse);
  } catch (err) {
    next(err);
  }
});

agendasRouter.get('/:id/items', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula(`/agendas/${req.params.id}/items`);
    // Convert items array to camelCase
    if (nebulaResponse.items) {
      nebulaResponse.items = nebulaResponse.items.map(snakeToCamel);
    }
    
    res.json(nebulaResponse);
  } catch (err) {
    next(err);
  }
});