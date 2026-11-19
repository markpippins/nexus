import { Injectable, signal, effect, computed } from '@angular/core';
import { Bookmark, NewBookmark } from '../models/bookmark.model.js';

const BOOKMARKS_STORAGE_KEY = 'file-explorer-bookmarks';

@Injectable({
  providedIn: 'root',
})
export class BookmarkService {
  private bookmarks = signal<Bookmark[]>([]);
  public allBookmarks = this.bookmarks.asReadonly();
  public bookmarkedLinks = computed(() => new Set(this.bookmarks().map(b => b.link)));

  constructor() {
    this.loadBookmarks();
    effect(() => {
      this.saveBookmarks();
    });
  }

  private loadBookmarks(): void {
    try {
      const stored = localStorage.getItem(BOOKMARKS_STORAGE_KEY);
      if (stored) {
        this.bookmarks.set(JSON.parse(stored));
      }
    } catch (e) {
      console.error('Failed to load bookmarks from localStorage', e);
    }
  }

  private saveBookmarks(): void {
    try {
      localStorage.setItem(BOOKMARKS_STORAGE_KEY, JSON.stringify(this.bookmarks()));
    } catch (e) {
      console.error('Failed to save bookmarks to localStorage', e);
    }
  }

  addBookmark(path: string[], data: NewBookmark): void {
    const newBookmark: Bookmark = {
      ...data,
      _id: `bm_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`,
      _creationTime: Date.now(),
      path: path.join('/'),
    };
    this.bookmarks.update(current => [...current, newBookmark]);
  }

  deleteBookmark(bookmarkId: string): void {
    this.bookmarks.update(current => current.filter(b => b._id !== bookmarkId));
  }

  findBookmarkByLink(link: string): Bookmark | undefined {
    return this.bookmarks().find(b => b.link === link);
  }
}
