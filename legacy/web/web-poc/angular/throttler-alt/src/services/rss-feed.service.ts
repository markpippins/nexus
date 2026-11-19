import { Injectable, signal, inject } from '@angular/core';
import { RssFeed } from '../models/rss-feed.model.js';
import { DbService } from './db.service.js';
import { ToastService } from './toast.service.js';

const DEFAULT_FEEDS: Omit<RssFeed, 'id'>[] = [
  { name: 'Angular Blog', url: 'angular.io/blog' },
  { name: 'Smashing Magazine', url: 'smashingmagazine.com/feed' },
];

@Injectable({
  providedIn: 'root',
})
export class RssFeedService {
  private dbService = inject(DbService);
  private toastService = inject(ToastService);
  
  feeds = signal<RssFeed[]>([]);

  constructor() {
    this.loadFeeds();
  }

  private async loadFeeds(): Promise<void> {
    try {
      let feeds = await this.dbService.getAllFeeds();
      if (feeds.length === 0) {
        // First-time setup with defaults
        const defaultFeedsWithIds = DEFAULT_FEEDS.map(f => ({ ...f, id: this.generateId() }));
        for (const feed of defaultFeedsWithIds) {
          await this.dbService.addFeed(feed);
        }
        feeds = defaultFeedsWithIds;
      }
      this.feeds.set(feeds);
    } catch (e) {
      console.error('Failed to load RSS feeds from IndexedDB', e);
      this.toastService.show('Could not load RSS feeds.', 'error');
    }
  }
  
  private generateId(): string {
    return `rss-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
  }

  async addFeed(feedData: Omit<RssFeed, 'id'>): Promise<void> {
    const newFeed: RssFeed = { ...feedData, id: this.generateId() };
    await this.dbService.addFeed(newFeed);
    this.feeds.update(feeds => [...feeds, newFeed].sort((a,b) => a.name.localeCompare(b.name)));
    this.toastService.show(`Feed "${newFeed.name}" added.`);
  }

  async updateFeed(updatedFeed: RssFeed): Promise<void> {
    await this.dbService.updateFeed(updatedFeed);
    this.feeds.update(feeds => 
      feeds.map(f => f.id === updatedFeed.id ? updatedFeed : f).sort((a,b) => a.name.localeCompare(b.name))
    );
    this.toastService.show(`Feed "${updatedFeed.name}" updated.`);
  }

  async deleteFeed(id: string): Promise<void> {
    await this.dbService.deleteFeed(id);
    this.feeds.update(feeds => feeds.filter(f => f.id !== id));
    this.toastService.show('Feed deleted.');
  }
}
