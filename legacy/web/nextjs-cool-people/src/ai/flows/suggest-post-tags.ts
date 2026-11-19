'use server';

/**
 * @fileOverview A flow to suggest relevant tags for a user's post using AI.
 *
 * - suggestPostTags - A function that handles the tag suggestion process.
 * - SuggestPostTagsInput - The input type for the suggestPostTags function.
 * - SuggestPostTagsOutput - The return type for the suggestPostTags function.
 */

import {ai} from '@/ai/genkit';
import {z} from 'genkit';

const SuggestPostTagsInputSchema = z.object({
  postContent: z
    .string()
    .describe('The content of the post for which tags are to be suggested.'),
});
export type SuggestPostTagsInput = z.infer<typeof SuggestPostTagsInputSchema>;

const SuggestPostTagsOutputSchema = z.object({
  suggestedTags: z
    .array(z.string())
    .describe('An array of suggested tags for the post.'),
});
export type SuggestPostTagsOutput = z.infer<typeof SuggestPostTagsOutputSchema>;

export async function suggestPostTags(input: SuggestPostTagsInput): Promise<SuggestPostTagsOutput> {
  return suggestPostTagsFlow(input);
}

const prompt = ai.definePrompt({
  name: 'suggestPostTagsPrompt',
  input: {schema: SuggestPostTagsInputSchema},
  output: {schema: SuggestPostTagsOutputSchema},
  prompt: `You are a social media expert. Your job is to suggest tags for user posts so that they are more discoverable.

  Suggest 5 tags for the following post:
  {{postContent}}

  Make sure the tags are relevant to the post.
  Format the tags as a JSON array of strings. Do not include any other text besides the JSON array.
  `,
});

const suggestPostTagsFlow = ai.defineFlow(
  {
    name: 'suggestPostTagsFlow',
    inputSchema: SuggestPostTagsInputSchema,
    outputSchema: SuggestPostTagsOutputSchema,
  },
  async input => {
    const {output} = await prompt(input);
    return output!;
  }
);
