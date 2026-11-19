import { Injectable, signal } from '@angular/core';
import { FileSystemNode } from '../models/file-system.model.js';
import { FileSystemProvider } from './file-system-provider.js';
import { NewBookmark } from '../models/bookmark.model.js';

export interface FileSystemDragPayload {
  sourceProvider: FileSystemProvider;
  sourcePath: string[];
  items: FileSystemNode[];
}

export interface BookmarkDragPayload {
  data: NewBookmark;
}

export type DragDropPayload = 
  | { type: 'filesystem', payload: FileSystemDragPayload }
  | { type: 'bookmark', payload: BookmarkDragPayload };


@Injectable({
  providedIn: 'root',
})
export class DragDropService {
  dragPayload = signal<DragDropPayload | null>(null);

  startDrag(payload: DragDropPayload): void {
    this.dragPayload.set(payload);
  }

  getPayload(): DragDropPayload | null {
    return this.dragPayload();
  }

  endDrag(): void {
    this.dragPayload.set(null);
  }
}
