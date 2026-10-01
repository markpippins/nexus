import { Router } from 'express';

export const healthRouter = Router();

healthRouter.get('/', async (_req, res) => {
  res.json({ status: 'healthy' });
});
