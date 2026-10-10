import { Router } from 'express';
import { fetchNebula, snakeToCamel } from '../utils/fetchNebula.js';
import { NotFoundError } from '../errors.js';

export const requirementsRouter = Router();

requirementsRouter.get('/', async (req, res, next) => {
  try {
    const { page = 1, pageSize = 100 } = req.query;
    const data = await fetchNebula('/requirements', { page, pageSize });
    res.json({
      items: (data.items || []).map(snakeToCamel),
      total: data.total || 0,
      page: data.page || 1,
      pageSize: data.pageSize || 100,
    });
  } catch (err) {
    next(err);
  }
});

requirementsRouter.get('/:id', async (req, res, next) => {
  try {
    const data = await fetchNebula(`/requirements/${req.params.id}`);
    if (!data) throw new NotFoundError('Not found');
    res.json(snakeToCamel(data));
  } catch (err) {
    next(err);
  }
});
