import { Router } from 'express';
import { fetchNebula, snakeToCamel } from '../utils/fetchNebula.js';

export const conversationsRouter = Router();

/**
 * Proxy conversation snapshots to nebula-srv (nebula owns
 * GET /api/conversations). Forwarded for the list and single-item
 * lookups, with snake_case -> camelCase normalization so the
 * frontend contract stays stable.
 */
conversationsRouter.get('/', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula('/conversations', {
      ...req.query,
      page: req.query.page || '1',
      pageSize: req.query.pageSize || '100',
    });

    if (nebulaResponse.items) {
      nebulaResponse.items = nebulaResponse.items.map(snakeToCamel);
    }

    res.json(nebulaResponse);
  } catch (err) {
    next(err);
  }
});

conversationsRouter.get('/:id', async (req, res, next) => {
  try {
    // nebula exposes single-snapshot lookup as by-snapshot/:id
    const nebulaResponse = await fetchNebula(`/conversations/by-snapshot/${req.params.id}`);
    if (!nebulaResponse || !nebulaResponse.id) {
      res.status(404).json({ error: 'Not found' });
      return;
    }
    res.json(snakeToCamel(nebulaResponse));
  } catch (err) {
    next(err);
  }
});
