'use server';
/**
 * @fileOverview An AI agent that generates summaries of alerts, including possible root causes and recommended actions.
 *
 * - generateAlertSummaries - A function that handles the alert summary generation process.
 * - GenerateAlertSummariesInput - The input type for the generateAlertSummaries function.
 * - GenerateAlertSummariesOutput - The return type for the generateAlertSummaries function.
 */

import {ai} from '@/ai/genkit';
import {z} from 'genkit';

const GenerateAlertSummariesInputSchema = z.object({
  alertDetails: z
    .string()
    .describe('Detailed information about the alerts that need to be summarized.'),
});
export type GenerateAlertSummariesInput = z.infer<typeof GenerateAlertSummariesInputSchema>;

const GenerateAlertSummariesOutputSchema = z.object({
  summary: z.string().describe('A concise summary of the alerts.'),
  possibleRootCauses: z
    .string()
    .describe('Possible root causes of the alerts.'),
  recommendedActions: z
    .string()
    .describe('Recommended actions to resolve the alerts.'),
});
export type GenerateAlertSummariesOutput = z.infer<typeof GenerateAlertSummariesOutputSchema>;

export async function generateAlertSummaries(
  input: GenerateAlertSummariesInput
): Promise<GenerateAlertSummariesOutput> {
  return generateAlertSummariesFlow(input);
}

const prompt = ai.definePrompt({
  name: 'generateAlertSummariesPrompt',
  input: {schema: GenerateAlertSummariesInputSchema},
  output: {schema: GenerateAlertSummariesOutputSchema},
  prompt: `You are an expert system administrator tasked with summarizing alerts and providing root causes and recommended actions.

  Summarize the following alert details, identify possible root causes, and suggest recommended actions to resolve the issues.  Be as specific as possible.

  Alert Details: {{{alertDetails}}}`,
});

const generateAlertSummariesFlow = ai.defineFlow(
  {
    name: 'generateAlertSummariesFlow',
    inputSchema: GenerateAlertSummariesInputSchema,
    outputSchema: GenerateAlertSummariesOutputSchema,
  },
  async input => {
    const {output} = await prompt(input);
    return output!;
  }
);
