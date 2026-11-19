import { Injectable } from '@angular/core';
import { timer, firstValueFrom } from 'rxjs';
import { map } from 'rxjs/operators';

@Injectable({
  providedIn: 'root',
})
export class GeminiService {
  search(query: string): Promise<string> {
    console.log(`Simulating Gemini search for: ${query}`);
    const result$ = timer(800).pipe(
      map(
        () =>
`Certainly! Here is a summary based on your query for "${query}":

**Key Points:**
- **Definition:** ${query} is a concept/term that is often discussed in various fields.
- **History:** Its origins can be traced back to ancient times, evolving significantly in the 20th century.
- **Modern Relevance:** Today, ${query} plays a crucial role in technology, science, and culture, impacting daily life in numerous ways.

This is a generated summary. For more detailed information, consider consulting academic sources or official documentation.`
      )
    );
    return firstValueFrom(result$);
  }
}
