import { Router } from 'express';
import { NotFoundError } from '../errors.js';
import { fetchNebula, snakeToCamel } from '../utils/fetchNebula.js';

export const plansRouter = Router();

// Shape note: nebula-srv /api/plans returns a different field set.
// normalizePlanItem in fetchNebula.js handles default field population.
plansRouter.get('/', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula('/plans', {
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

plansRouter.get('/:id', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula(`/plans/${req.params.id}`);
    if (!nebulaResponse || !nebulaResponse.id) {
      throw new NotFoundError('Not found');
    }
    res.json(snakeToCamel(nebulaResponse));
  } catch (err) {
    next(err);
  }
});
