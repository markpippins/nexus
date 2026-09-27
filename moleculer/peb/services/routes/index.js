import { Router } from 'express';
import { fleetHealthRouter } from './fleet-health.js';
import { eventsRouter } from './events.js';
import { transactionsRouter } from './transactions.js';
import { bindingDecisionsRouter } from './binding-decisions.js';
import { decisionsRouter } from './decisions.js';
import { tracesRouter } from './traces.js';
import { entitiesRouter } from './entities.js';
import { stateRouter } from './state.js';
import { streamRouter } from './stream.js';
import { healthRouter } from './health.js';

export const routes = Router();

routes.use('/health', fleetHealthRouter);
// IMPORTANT: /events/stream must be mounted BEFORE /events, because the
// eventsRouter has a `/:receipt_id` path-param route that would otherwise
// swallow "stream" as a receipt id and return 404 for the SSE endpoint.
routes.use('/events/stream', streamRouter);
routes.use('/events', eventsRouter);
// Binding decisions are a transaction-adjacent read-only projection, but
// must be mounted before /transactions/:id-style consumers.
routes.use('/binding-decisions', bindingDecisionsRouter);
routes.use('/transactions', transactionsRouter);
routes.use('/decisions', decisionsRouter);
routes.use('/traces', tracesRouter);
routes.use('/entities', entitiesRouter);
routes.use('/state', stateRouter);

// healthRouter is for the root /health probe mounted in src/index.js.
export { healthRouter };
