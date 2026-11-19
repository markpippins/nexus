import { Injectable, inject } from '@angular/core';
import { BrokerService } from './broker.service.js';
import { SearchResultNode } from '../models/file-system.model.js';
import { GoogleSearchResult } from '../models/google-search-result.model.js';
import { ImageSearchResult } from '../models/image-search-result.model.js';
import { YoutubeSearchResult } from '../models/youtube-search-result.model.js';
import { AcademicSearchResult } from '../models/academic-search-result.model.js';

const FILE_SEARCH_SERVICE_NAME = 'searchService';
const STREAM_SEARCH_SERVICE_NAME = 'streamSearchService';

@Injectable({
  providedIn: 'root',
})
export class SearchService {
  private brokerService = inject(BrokerService);

  /**
   * Performs a file search against a remote search service.
   * @param searchUrl The URL of the broker endpoint for the search service.
   * @param alias The user or profile alias for context.
   * @param path The path to search within.
   * @param query The search query string.
   * @returns A promise that resolves to an array of file search results.
   */
  search(searchUrl: string, alias: string, path: string[], query: string): Promise<SearchResultNode[]> {
    const operation = 'search'; // Assuming the backend operation is named 'search'

    const params = {
      alias,
      path,
      query,
    };

    return this.brokerService.submitRequest<SearchResultNode[]>(searchUrl, FILE_SEARCH_SERVICE_NAME, operation, params);
  }

  // --- Methods for Idea Stream ---

  /**
   * Performs a Google Public search via the broker.
   * @param searchUrl The URL of the broker endpoint.
   * @param query The search query.
   */
  googleSearch(searchUrl: string, query: string): Promise<GoogleSearchResult[]> {
    const operation = 'googlePublicSearch';
    const params = { query };
    return this.brokerService.submitRequest<GoogleSearchResult[]>(searchUrl, STREAM_SEARCH_SERVICE_NAME, operation, params);
  }

  /**
   * Performs an image search via the broker (e.g., Unsplash).
   * @param searchUrl The URL of the broker endpoint.
   * @param query The search query.
   */
  imageSearch(searchUrl: string, query: string): Promise<ImageSearchResult[]> {
    const operation = 'imageSearch';
    const params = { query };
    return this.brokerService.submitRequest<ImageSearchResult[]>(searchUrl, STREAM_SEARCH_SERVICE_NAME, operation, params);
  }
  
  /**
   * Performs a YouTube video search via the broker.
   * @param searchUrl The URL of the broker endpoint.
   * @param query The search query.
   */
  youtubeSearch(searchUrl: string, query: string): Promise<YoutubeSearchResult[]> {
    const operation = 'youtubeSearch';
    const params = { query };
    return this.brokerService.submitRequest<YoutubeSearchResult[]>(searchUrl, STREAM_SEARCH_SERVICE_NAME, operation, params);
  }

  /**
   * Performs an academic search via the broker.
   * @param searchUrl The URL of the broker endpoint.
   * @param query The search query.
   */
  academicSearch(searchUrl: string, query: string): Promise<AcademicSearchResult[]> {
    const operation = 'academicSearch';
    const params = { query };
    return this.brokerService.submitRequest<AcademicSearchResult[]>(searchUrl, STREAM_SEARCH_SERVICE_NAME, operation, params);
  }

  /**
   * Performs a Gemini search via the broker.
   * @param searchUrl The URL of the broker endpoint.
   * @param query The search query.
   * @returns A promise resolving to the raw text response.
   */
  async geminiSearch(searchUrl: string, query: string): Promise<string> {
    const operation = 'geminiSearch';
    const params = { query };
    // The broker response is likely an object, so we'll assume it has a `text` property.
    const response = await this.brokerService.submitRequest<{ text: string }>(searchUrl, STREAM_SEARCH_SERVICE_NAME, operation, params);
    return response.text;
  }
}
