import { Router } from 'express';
import { NotFoundError } from '../errors.js';
import { fetchNebula, snakeToCamel } from '../utils/fetchNebula.js';

export const harvestsRouter = Router();

harvestsRouter.get('/', async (req, res, next) => {
  try {
    // Forward everything nebula-srv /api/harvests honors. nebula-srv accepts
    // page, pageSize, sort, keyword, tag, search, dateFrom, dateTo, model,
    // version, sourceHash, level, visibilityScope, systemId, subsystemId,
    // featureId. Map limit/offset (legacy UI shape) onto page/pageSize.
    const pageSize = req.query.pageSize ?? req.query.limit ?? '100';
    const page = req.query.page ?? (req.query.offset ? String(Math.max(1, Math.floor(parseInt(req.query.offset, 10) / parseInt(pageSize, 10)) + 1)) : '1');
    const query = {
      page,
      pageSize,
    };
    for (const key of ['model', 'version', 'sourceHash', 'level', 'visibilityScope',
                       'tag', 'search', 'sort', 'keyword', 'dateFrom', 'dateTo',
                       'systemId', 'subsystemId', 'featureId']) {
      if (req.query[key] !== undefined) query[key] = req.query[key];
    }
    const nebulaResponse = await fetchNebula('/harvests', query);
    if (nebulaResponse.items) {
      nebulaResponse.items = nebulaResponse.items.map(snakeToCamel);
    }
    res.json(nebulaResponse);
  } catch (err) {
    next(err);
  }
});

harvestsRouter.get('/:id', async (req, res, next) => {
  try {
    const nebulaResponse = await fetchNebula(`/harvests/${req.params.id}`);
    if (!nebulaResponse || !nebulaResponse.id) {
      throw new NotFoundError('Not found');
    }
    res.json(snakeToCamel(nebulaResponse));
  } catch (err) {
    next(err);
  }
});
