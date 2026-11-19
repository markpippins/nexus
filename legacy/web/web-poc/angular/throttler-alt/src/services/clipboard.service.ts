import { Injectable, signal } from '@angular/core';
import { FileSystemNode } from '../models/file-system.model.js';
import { FileSystemProvider } from './file-system-provider.js';

export interface ClipboardPayload {
  operation: 'cut' | 'copy';
  sourceProvider: FileSystemProvider;
  sourcePath: string[];
  items: FileSystemNode[];
}

@Injectable({
  providedIn: 'root',
})
export class ClipboardService {
  clipboard = signal<ClipboardPayload | null>(null);

  set(payload: ClipboardPayload): void {
    this.clipboard.set(payload);
  }

  get(): ClipboardPayload | null {
    return this.clipboard();
  }

  clear(): void {
    this.clipboard.set(null);
  }
}