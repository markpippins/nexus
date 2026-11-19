import { config } from 'dotenv';
config();

import '@/ai/flows/summarize-log-events.ts';
import '@/ai/flows/generate-alert-summaries.ts';
import '@/ai/flows/flag-anomalous-events.ts';