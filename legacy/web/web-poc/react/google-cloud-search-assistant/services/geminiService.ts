
import { GoogleGenAI, GenerateContentResponse } from "@google/genai";
import { GroundingChunk, SearchResult } from '../types';

if (!process.env.API_KEY) {
  throw new Error("API_KEY environment variable not set");
}

const ai = new GoogleGenAI({ apiKey: process.env.API_KEY });

export async function performGoogleSearch(query: string): Promise<SearchResult> {
  try {
    const response: GenerateContentResponse = await ai.models.generateContent({
      model: "gemini-2.5-flash",
      contents: query,
      config: {
        tools: [{ googleSearch: {} }],
      },
    });

    const text = response.text;
    const sources: GroundingChunk[] = response.candidates?.[0]?.groundingMetadata?.groundingChunks || [];
    
    return {
      text,
      sources: sources.filter(chunk => chunk.web && chunk.web.uri), // Ensure only web sources are included
      rawResponse: response,
    };
  } catch (error) {
    console.error("Error performing Google Search via Gemini API:", error);
    if (error instanceof Error) {
        throw new Error(`Failed to fetch search results: ${error.message}`);
    }
    throw new Error("An unknown error occurred during the search.");
  }
}
