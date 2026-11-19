import { Injectable, signal, WritableSignal } from '@angular/core';

@Injectable({
  providedIn: 'root',
})
export class TextEditorService {
  viewRequest = signal<{ contentSignal: WritableSignal<string>; title: string; fileName: string } | null>(null);

  open(contentSignal: WritableSignal<string>, title: string, fileName: string): void {
    this.viewRequest.set({ contentSignal, title, fileName });
  }

  close(): void {
    this.viewRequest.set(null);
  }
}
