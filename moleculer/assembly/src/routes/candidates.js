import { Router } from 'express';
import { NotFoundError } from '../errors.js';
import { fetchNebula, snakeToCamel } from '../utils/fetchNebula.js';

export const candidatesRouter = Router();

// Path remapping: assembly-srv /api/candidates → nebula-srv /api/harvest-candidates
candidatesRouter.get('/', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula('/harvest-candidates', {
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

candidatesRouter.get('/:id', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula(`/harvest-candidates/${req.params.id}`);
    if (!nebulaResponse || !nebulaResponse.id) {
      throw new NotFoundError('Not found');
    }
    res.json(snakeToCamel(nebulaResponse));
  } catch (err) {
    next(err);
  }
});
