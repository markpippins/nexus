import { Router } from 'express';
import { healthRouter } from './health.js';
import { fieldTypesRouter } from './field-types.js';
import { fieldsRouter } from './fields.js';
import { objectsRouter } from './objects.js';
import { encodeRouter } from './encode.js';
import { stereotypesRouter } from './stereotypes.js';

export const routes = Router();

routes.use('/health', healthRouter);
routes.use('/field-types', fieldTypesRouter);
routes.use('/fields', fieldsRouter);
routes.use('/objects', objectsRouter);
routes.use('/encode', encodeRouter);
routes.use('/stereotypes', stereotypesRouter);
