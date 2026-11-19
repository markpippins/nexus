'use server';
/**
 * @fileOverview Summarizes log events within a specified timeframe.
 *
 * - summarizeLogEvents - A function that summarizes log events.
 * - SummarizeLogEventsInput - The input type for the summarizeLogEvents function.
 * - SummarizeLogEventsOutput - The return type for the summarizeLogEvents function.
 */

import {ai} from '@/ai/genkit';
import {z} from 'genkit';

const SummarizeLogEventsInputSchema = z.object({
  logEvents: z.string().describe('The log events to summarize.'),
  timeframe: z.string().describe('The timeframe for the log events.'),
});
export type SummarizeLogEventsInput = z.infer<typeof SummarizeLogEventsInputSchema>;

const SummarizeLogEventsOutputSchema = z.object({
  summary: z.string().describe('A summary of the log events within the specified timeframe.'),
});
export type SummarizeLogEventsOutput = z.infer<typeof SummarizeLogEventsOutputSchema>;

export async function summarizeLogEvents(input: SummarizeLogEventsInput): Promise<SummarizeLogEventsOutput> {
  return summarizeLogEventsFlow(input);
}

const prompt = ai.definePrompt({
  name: 'summarizeLogEventsPrompt',
  input: {schema: SummarizeLogEventsInputSchema},
  output: {schema: SummarizeLogEventsOutputSchema},
  prompt: `You are an expert system administrator tasked with summarizing log events.
\n  Summarize the following log events that occurred within the timeframe of {{{timeframe}}}.\n\n  Log Events:\n  {{{logEvents}}}\n\n  Summary: `,
});

const summarizeLogEventsFlow = ai.defineFlow(
  {
    name: 'summarizeLogEventsFlow',
    inputSchema: SummarizeLogEventsInputSchema,
    outputSchema: SummarizeLogEventsOutputSchema,
  },
  async input => {
    const {output} = await prompt(input);
    return output!;
  }
);
