import { Router } from 'express';
import { forumsRouter } from './forums.js';
import { feedRouter } from './feed.js';
import { healthRouter } from './health.js';
import { refreshStatsRouter } from './refresh-stats.js';
import { workRequestsRouter } from './work-requests.js';
import { requirementsRouter } from './requirements.js';
import { agendasRouter } from './agendas.js';
import { candidatesRouter } from './candidates.js';
import { harvestsRouter } from './harvests.js';
import { conversationsRouter } from './conversations.js';

import { openQuestionsRouter } from './open-questions.js';
import { assessmentsRouter } from './assessments.js';
import { observationsRouter } from './observations.js';
import { agentRecordsRouter } from './agent-records.js';
import { specificationsRouter } from './specifications.js';
import { usersRouter } from './users.js';

import { countsRouter } from './counts.js';
import { plansRouter } from './plans.js';
import { searchRouter } from './search.js';
import { bridgesRouter } from './bridges.js';
import { dualityRouter } from './duality.js';
import { decisionsRouter } from './decisions.js';
import { segmentSetsRouter } from './segment-sets.js';

export const routes = Router();

routes.use('/health', healthRouter);
routes.use('/refresh-stats', refreshStatsRouter);
routes.use('/counts', countsRouter);
routes.use('/search', searchRouter);
routes.use('/forums', forumsRouter);
routes.use('/feed', feedRouter);
routes.use('/work-requests', workRequestsRouter);
routes.use('/requirements', requirementsRouter);
routes.use('/agendas', agendasRouter);
routes.use('/candidates', candidatesRouter);
// segment-sets router defines FULL domain paths (/candidates/:id/segment-sets,
// /requirements/:id/segment-sets, /intent-records/:id/segment-sets) plus its
// own /segment-sets list+get — mounted LAST so its `/:id` route cannot shadow
// other mounts (Express tries layers in registration order and this router
// has a root-relative `/:id`).
routes.use('/harvests', harvestsRouter);
routes.use('/conversations', conversationsRouter);

routes.use('/open-questions', openQuestionsRouter);
routes.use('/assessments', assessmentsRouter);
routes.use('/observations', observationsRouter);
routes.use('/agent-records', agentRecordsRouter);
routes.use('/specifications', specificationsRouter);
routes.use('/users', usersRouter);

routes.use('/plans', plansRouter);
routes.use('/bridges', bridgesRouter);
routes.use('/duality', dualityRouter);
routes.use('/decisions', decisionsRouter);
routes.use('/', segmentSetsRouter);
