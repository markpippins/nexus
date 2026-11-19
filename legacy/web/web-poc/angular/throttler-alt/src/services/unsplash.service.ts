import { Injectable } from '@angular/core';
import { ImageSearchResult } from '../models/image-search-result.model.js';
import { timer, firstValueFrom } from 'rxjs';
import { map } from 'rxjs/operators';

@Injectable({
  providedIn: 'root',
})
export class UnsplashService {
  search(query: string): Promise<ImageSearchResult[]> {
    console.log(`Simulating Unsplash (image) search for: ${query}`);

    // Simple hashing function to get a somewhat consistent "random" seed from the query
    const seed = query.split('').reduce((acc, char) => char.charCodeAt(0) + ((acc << 5) - acc), 0);

    const results$ = timer(500).pipe(
      map(() =>
        Array.from({ length: 12 }).map((_, i) => {
          const id = `${seed}_${i}`;
          const width = 200 + (i % 4) * 50;
          const height = 200 + (i % 3) * 50;
          return {
            id: id,
            url: `https://picsum.photos/seed/${id}/1024/768`,
            thumbnailUrl: `https://picsum.photos/seed/${id}/${width}/${height}`,
            description: `A descriptive caption for an image related to ${query}.`,
            photographer: 'Mock Photographer',
            source: 'picsum.photos',
            publishedAt: new Date(Date.now() - i * 1000 * 60 * 60 * 24 * 3).toISOString(),
          };
        })
      )
    );
    return firstValueFrom(results$);
  }
}