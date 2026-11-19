'use server';

/**
 * @fileOverview This file implements the Genkit flow for providing smart suggestions for new folder or file names.
 *
 * It leverages generative AI to suggest names based on existing directory contents and the user's alias.
 *
 * @exported smartNameSuggestions - An async function that takes user alias and directory contents as input and returns name suggestions.
 * @exported SmartNameSuggestionsInput - The input type for the smartNameSuggestions function.
 * @exported SmartNameSuggestionsOutput - The return type for the smartNameSuggestions function.
 */

import {ai} from '@/ai/genkit';
import {z} from 'genkit';

const SmartNameSuggestionsInputSchema = z.object({
  alias: z.string().describe('The user alias.'),
  directoryContents: z.array(z.string()).describe('The list of files and folders in the current directory.'),
  operationType: z.enum(['file', 'folder']).describe('The type of operation: file or folder.'),
});
export type SmartNameSuggestionsInput = z.infer<typeof SmartNameSuggestionsInputSchema>;

const SmartNameSuggestionsOutputSchema = z.object({
  suggestions: z.array(z.string()).describe('An array of suggested names for the new file or folder.'),
});
export type SmartNameSuggestionsOutput = z.infer<typeof SmartNameSuggestionsOutputSchema>;

export async function smartNameSuggestions(input: SmartNameSuggestionsInput): Promise<SmartNameSuggestionsOutput> {
  return smartNameSuggestionsFlow(input);
}

const prompt = ai.definePrompt({
  name: 'smartNameSuggestionsPrompt',
  input: {schema: SmartNameSuggestionsInputSchema},
  output: {schema: SmartNameSuggestionsOutputSchema},
  prompt: `You are a helpful assistant that suggests names for new files or folders based on the existing directory contents and user alias.

User Alias: {{{alias}}}
Existing Directory Contents: {{#each directoryContents}}{{{this}}}{{#unless @last}}, {{/unless}}{{/each}}
Operation Type: {{{operationType}}}

Based on this information, suggest 3 relevant names for the new {{operationType}}. Be concise.
Return only the array of name suggestions.
`,
});

const smartNameSuggestionsFlow = ai.defineFlow(
  {
    name: 'smartNameSuggestionsFlow',
    inputSchema: SmartNameSuggestionsInputSchema,
    outputSchema: SmartNameSuggestionsOutputSchema,
  },
  async input => {
    const {output} = await prompt(input);
    return output!;
  }
);
