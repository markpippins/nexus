import { Injectable, signal } from '@angular/core';

@Injectable({
  providedIn: 'root',
})
export class WebviewService {
  viewRequest = signal<{ url: string; title: string } | null>(null);

  open(url: string, title: string): void {
    this.viewRequest.set({ url, title });
  }

  close(): void {
    this.viewRequest.set(null);
  }
}
