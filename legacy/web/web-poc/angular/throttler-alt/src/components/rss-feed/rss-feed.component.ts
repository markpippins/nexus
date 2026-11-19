import { Component, ChangeDetectionStrategy, input, computed, signal, OnInit, inject, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WebviewService } from '../../services/webview.service.js';
import { RssFeedService } from '../../services/rss-feed.service.js';
import { RssFeed } from '../../models/rss-feed.model.js';

interface RssItem {
  title: string;
  source: string;
  date: string;
  snippet: string;
  link: string;
}

@Component({
  selector: 'app-rss-feed',
  imports: [CommonModule],
  templateUrl: './rss-feed.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RssFeedComponent implements OnInit {
  filterQuery = input('');
  manageFeeds = output<void>();

  private webviewService = inject(WebviewService);
  private rssFeedService = inject(RssFeedService);

  // --- State Signals ---
  feeds = this.rssFeedService.feeds;
  selectedFeed = signal<RssFeed | null>(null);
  articles = signal<RssItem[]>([]);
  isLoading = signal(false);
  isFeedDropdownOpen = signal(false);

  // --- Computed Signals ---
  filteredItems = computed(() => {
    const feedArticles = this.articles() ?? [];
    const query = this.filterQuery().toLowerCase();

    if (!query) {
      return feedArticles;
    }
    return feedArticles.filter(item => 
      item.title.toLowerCase().includes(query) ||
      item.snippet.toLowerCase().includes(query)
    );
  });
  
  ngOnInit(): void {
    const firstFeed = this.feeds()[0];
    if (firstFeed) {
      this.selectFeed(firstFeed);
    }
  }

  private setupMockArticlesForFeed(feed: RssFeed): void {
    // This is a mock implementation for fetching articles.
    // A real implementation would use HttpClient to fetch and parse the RSS feed.
    const mockArticles: RssItem[] = [
      {
        title: `Article 1 for ${feed.name}`,
        source: feed.name,
        date: '2 hours ago',
        snippet: 'The new release introduces significant improvements to the rendering engine and build times...',
        link: `https://${feed.url}`,
      },
      {
        title: `Article 2 for ${feed.name}`,
        source: feed.name,
        date: '1 day ago',
        snippet: 'A deep dive into how signals are changing state management for the better in modern Angular apps.',
        link: `https://${feed.url}`,
      },
    ];
    this.articles.set(mockArticles);
  }

  toggleFeedDropdown(event: MouseEvent): void {
    event.stopPropagation();
    this.isFeedDropdownOpen.update(v => !v);
  }

  selectFeed(feed: RssFeed): void {
    this.selectedFeed.set(feed);
    this.isFeedDropdownOpen.set(false);
    this.refreshFeed();
  }

  refreshFeed(): void {
    const feed = this.selectedFeed();
    if (!feed || this.isLoading()) return;

    this.isLoading.set(true);
    // Simulate network delay for fetching articles
    setTimeout(() => {
      this.setupMockArticlesForFeed(feed); // Use mock data for now
      this.isLoading.set(false);
    }, 500);
  }

  openArticle(item: RssItem): void {
    this.webviewService.open(item.link, item.title);
  }

  onManageFeedsClick(): void {
    this.isFeedDropdownOpen.set(false); // Close dropdown if open
    this.manageFeeds.emit();
  }
}
