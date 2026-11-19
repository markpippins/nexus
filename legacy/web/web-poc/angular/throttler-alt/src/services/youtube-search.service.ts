import { Injectable } from '@angular/core';
import { YoutubeSearchResult } from '../models/youtube-search-result.model.js';
import { timer, firstValueFrom } from 'rxjs';
import { map } from 'rxjs/operators';

@Injectable({
  providedIn: 'root',
})
export class YoutubeSearchService {
  search(query: string): Promise<YoutubeSearchResult[]> {
    console.log(`Simulating YouTube search for: ${query}`);
    const results$ = timer(600).pipe(
      map(() => [
        {
          videoId: 'dQw4w9WgXcQ',
          title: `A Guide to Understanding ${query}`,
          description: `This video provides a comprehensive tutorial on ${query}, from the basic concepts to advanced techniques. Perfect for beginners and experts alike.`,
          thumbnailUrl: 'https://picsum.photos/seed/yt1/320/180',
          channelTitle: 'Tech Tutorials',
          publishedAt: '2023-11-10T14:00:00Z',
        },
        {
          videoId: 'oHg5SJYRHA0',
          title: `Top 5 Misconceptions About ${query}`,
          description: `We debunk the most common myths and misconceptions surrounding ${query}. You might be surprised by what you learn!`,
          thumbnailUrl: 'https://picsum.photos/seed/yt2/320/180',
          channelTitle: 'Myth Busters',
          publishedAt: '2024-03-22T18:00:00Z',
        },
        {
          videoId: 'YddwkMJG1Jo',
          title: `The History of ${query}: A Documentary`,
          description: `Explore the fascinating history and evolution of ${query} in this in-depth documentary, featuring interviews with leading experts.`,
          thumbnailUrl: 'https://picsum.photos/seed/yt3/320/180',
          channelTitle: 'History Explained',
          publishedAt: '2022-07-19T10:00:00Z',
        },
      ])
    );
    return firstValueFrom(results$);
  }
}