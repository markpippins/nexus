
export interface GroundingChunk {
  web?: {
    // FIX: Aligned with @google/genai's GroundingChunk type. The `uri` and `title`
    // properties are optional in the API response, so they are marked as optional here
    // to resolve the type assignment error in `services/geminiService.ts`.
    uri?: string;
    title?: string;
  };
}

export interface SearchResult {
  text: string;
  sources: GroundingChunk[];
  rawResponse: object;
}
