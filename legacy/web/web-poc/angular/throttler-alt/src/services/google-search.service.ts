import { Injectable } from '@angular/core';
import { GoogleSearchResult } from '../models/google-search-result.model.js';
import { timer, firstValueFrom } from 'rxjs';
import { map } from 'rxjs/operators';

@Injectable({
  providedIn: 'root',
})
export class GoogleSearchService {
  search(query: string): Promise<GoogleSearchResult[]> {
    console.log(`Simulating Google search for: ${query}`);
    const results$ = timer(300).pipe(
      map(() => [
        {
          title: `What is ${query}? - Wikipedia`,
          link: '#',
          snippet: `An article explaining the definition, history, and significance of ${query}. It covers various aspects and provides a comprehensive overview.`,
          source: 'wikipedia.org',
          publishedAt: '2024-05-20T10:00:00Z',
        },
        {
          title: `The Ultimate Guide to ${query} in 2024`,
          link: '#',
          snippet: `Everything you need to know about ${query}. Our guide covers the best practices, common uses, and expert tips to get you started.`,
          source: 'tech-guide.com',
          publishedAt: '2024-05-28T12:30:00Z',
        },
        {
          title: `Official Documentation for ${query}`,
          link: '#',
          snippet: `The official documentation provides a complete reference, tutorials, and API information for working with ${query}.`,
          source: 'official-docs.io',
          publishedAt: '2024-01-15T09:00:00Z',
        },
        {
          title: `Recent News and Developments about ${query}`,
          link: '#',
          snippet: `Stay up to date with the latest articles, research, and news related to ${query} from top sources around the world.`,
          source: 'news-aggregator.com',
          publishedAt: '2024-06-01T18:45:00Z',
        },
      ])
    );
    return firstValueFrom(results$);
  }
}