export type BookmarkType = 'web' | 'image' | 'youtube' | 'academic' | 'gemini';

export interface Bookmark {
  _id: string;
  _creationTime: number;
  path: string; // The full path string, e.g., "Local Files/Documents"
  type: BookmarkType;
  title: string;
  link: string;
  snippet?: string;
  thumbnailUrl?: string;
  source: string; // e.g., "Google Search"
}

// Partial type for creating new bookmarks before they get an ID and path
export type NewBookmark = Omit<Bookmark, '_id' | '_creationTime' | 'path'>;
