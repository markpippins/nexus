import { Component, ChangeDetectionStrategy, output, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RssFeedService } from '../../services/rss-feed.service.js';
import { RssFeed } from '../../models/rss-feed.model.js';

type FormState = {
  id: string | null;
  name: string;
  url: string;
}

@Component({
  selector: 'app-rss-feeds-dialog',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './rss-feeds-dialog.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RssFeedsDialogComponent {
  rssFeedService = inject(RssFeedService);
  
  close = output<void>();

  selectedFeedId = signal<string | null>(null);
  formState = signal<FormState | null>(null);

  startAddNew(): void {
    this.formState.set({
      id: null,
      name: '',
      url: '',
    });
    this.selectedFeedId.set(null);
  }
  
  startEdit(feed: RssFeed): void {
    this.formState.set({ ...feed });
    this.selectedFeedId.set(feed.id);
  }
  
  cancelEdit(): void {
    this.formState.set(null);
    this.selectedFeedId.set(null);
  }

  async saveFeed(): Promise<void> {
    const state = this.formState();
    if (!state || !state.name.trim() || !state.url.trim()) return;

    if (state.id) { // Editing existing
      const updatedFeed: RssFeed = {
        id: state.id,
        name: state.name.trim(),
        url: state.url.trim(),
      };
      await this.rssFeedService.updateFeed(updatedFeed);
    } else { // Adding new
      const newFeedData = {
        name: state.name.trim(),
        url: state.url.trim(),
      };
      await this.rssFeedService.addFeed(newFeedData);
      this.formState.set(null);
      this.selectedFeedId.set(null);
    }
  }
  
  async deleteFeed(id: string): Promise<void> {
    if (confirm('Are you sure you want to delete this feed?')) {
      await this.rssFeedService.deleteFeed(id);
      if (this.formState()?.id === id) {
        this.formState.set(null);
      }
      this.selectedFeedId.set(null);
    }
  }

  onFormValueChange(event: Event, field: keyof Omit<FormState, 'id'>): void {
    const value = (event.target as HTMLInputElement).value;
    this.formState.update(state => state ? { ...state, [field]: value } : null);
  }
}
