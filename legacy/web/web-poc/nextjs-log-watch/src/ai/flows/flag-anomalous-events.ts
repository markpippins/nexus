// src/ai/flows/flag-anomalous-events.ts
'use server';
/**
 * @fileOverview This file defines a Genkit flow for flagging anomalous events in logs.
 *
 * - flagAnomalousEvents - A function that takes log entries and returns flagged anomalous events.
 * - FlagAnomalousEventsInput - The input type for the flagAnomalousEvents function.
 * - FlagAnomalousEventsOutput - The return type for the flagAnomalousEvents function.
 */

import {ai} from '@/ai/genkit';
import {z} from 'genkit';

const FlagAnomalousEventsInputSchema = z.object({
  logEntries: z.array(
    z.string().describe('A single log entry from the system.')
  ).describe('An array of log entries to be analyzed for anomalies.'),
});
export type FlagAnomalousEventsInput = z.infer<typeof FlagAnomalousEventsInputSchema>;

const FlagAnomalousEventsOutputSchema = z.object({
  anomalousEvents: z.array(
    z.object({
      logEntry: z.string().describe('The log entry that is considered anomalous.'),
      reason: z.string().describe('The reason why the log entry is flagged as anomalous.'),
    }).describe('A single anomalous event with its log entry and reason.')
  ).describe('An array of anomalous events detected in the logs.'),
});
export type FlagAnomalousEventsOutput = z.infer<typeof FlagAnomalousEventsOutputSchema>;

export async function flagAnomalousEvents(input: FlagAnomalousEventsInput): Promise<FlagAnomalousEventsOutput> {
  return flagAnomalousEventsFlow(input);
}

const flagAnomalousEventsPrompt = ai.definePrompt({
  name: 'flagAnomalousEventsPrompt',
  input: {schema: FlagAnomalousEventsInputSchema},
  output: {schema: FlagAnomalousEventsOutputSchema},
  prompt: `You are an AI Event Analyzer responsible for analyzing log entries and flagging anomalous events.

You will receive an array of log entries, and you must identify any events that are unusual, unexpected, or indicative of potential issues or security threats.
For each anomalous event, provide the log entry and a clear explanation of why it is considered anomalous.

Log Entries:
{{#each logEntries}}
- {{{this}}}
{{/each}}

Output the anomalous events in the format:
{
  "anomalousEvents": [
    {
      "logEntry": "<log entry>",
      "reason": "<reason for being anomalous>"
    },
    ...
  ]
}
`,
});

const flagAnomalousEventsFlow = ai.defineFlow(
  {
    name: 'flagAnomalousEventsFlow',
    inputSchema: FlagAnomalousEventsInputSchema,
    outputSchema: FlagAnomalousEventsOutputSchema,
  },
  async input => {
    const {output} = await flagAnomalousEventsPrompt(input);
    return output!;
  }
);
