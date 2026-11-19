import { SearchResult, SearchResultItem } from '../types';

const API_ENDPOINT = 'https://www.googleapis.com/customsearch/v1';

export async function performGoogleSearch(query: string, searchEngineId: string, apiKey: string): Promise<SearchResult> {
  if (!apiKey) {
    throw new Error('Search API Key is required. Please provide it on the configuration screen.');
  }

  if (!searchEngineId) {
    throw new Error('Search Engine ID is required. Please provide it on the configuration screen.');
  }

  const url = `${API_ENDPOINT}?key=${apiKey}&cx=${searchEngineId}&q=${encodeURIComponent(query)}`;

  try {
    const response = await fetch(url);
    const data = await response.json();

    if (!response.ok) {
      const errorMessage = data.error?.message || `Request failed with status ${response.status}`;
      throw new Error(errorMessage);
    }

    const items: SearchResultItem[] = data.items?.map((item: any) => ({
      title: item.title,
      link: item.link,
      snippet: item.snippet,
    })) || [];

    return {
      items: items,
      rawResponse: data,
    };
  } catch (error) {
    console.error('Error performing Google Custom Search:', error);
    if (error instanceof Error) {
      throw new Error(`Failed to fetch search results: ${error.message}`);
    }
    throw new Error('An unknown error occurred during the search.');
  }
}