import { Component, ChangeDetectionStrategy, signal, computed, ViewChild, ElementRef, inject, input, effect, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TextEditorService } from '../../services/note-dialog.service.js';
import { FileSystemProvider } from '../../services/file-system-provider.js';

// Declare the globals from the CDN scripts
declare var marked: { parse(markdown: string): string; };
declare var DOMPurify: { sanitize(dirty: string): string; };

const DEFAULT_NOTE_TEXT = '# Notes\n\n- Select a folder to view or create a note.\n- Notes are saved automatically as you type.';

@Component({
  selector: 'app-notes',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './notes.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotesComponent implements OnDestroy {
  private textEditorService = inject(TextEditorService);

  path = input.required<string[]>();
  provider = input.required<FileSystemProvider>();
  
  noteContent = signal<string>(DEFAULT_NOTE_TEXT);
  mode = signal<'edit' | 'preview'>('edit');
  isLoading = signal(false);
  private saveTimeout: any;

  @ViewChild('editor') editorTextarea: ElementRef<HTMLTextAreaElement> | undefined;

  isNoteAvailable = computed(() => {
    // A note is available if the current provider implements the note-taking methods.
    const provider = this.provider();
    return !!(provider.getNote && provider.saveNote);
  });

  renderedHtml = computed(() => {
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        const rawHtml = marked.parse(this.noteContent());
        return DOMPurify.sanitize(rawHtml);
    }
    // Provide a graceful fallback if the libraries fail to load
    return '<p>Error: Markdown parsing libraries not loaded.</p>';
  });

  constructor() {
    effect(() => {
      // When path or provider changes, load the note.
      this.path();
      this.provider();
      this.loadNote();
    }, { allowSignalWrites: true });

    effect(() => {
      // When content changes, schedule a save.
      const content = this.noteContent();
      this.scheduleSave(content);
    });
  }

  ngOnDestroy(): void {
    clearTimeout(this.saveTimeout);
  }

  private async loadNote(): Promise<void> {
    const p = this.path();
    const provider = this.provider();

    if (!this.isNoteAvailable() || !provider.getNote) {
      this.noteContent.set(DEFAULT_NOTE_TEXT);
      return;
    }
    
    this.isLoading.set(true);
    try {
      const providerPath = p.length > 0 ? p.slice(1) : [];
      const note = await provider.getNote(providerPath);
      this.noteContent.set(note ?? `# Notes for ${p.length > 0 ? p[p.length - 1] : 'Home'}\n\nThis note is empty. Start typing...`);
    } catch(e) {
      console.error('Failed to load note:', e);
      this.noteContent.set(`# Error\n\nCould not load note for this folder.`);
    } finally {
      this.isLoading.set(false);
    }
  }

  private scheduleSave(content: string): void {
    clearTimeout(this.saveTimeout);
    
    if (!this.isNoteAvailable()) {
      return;
    }

    this.saveTimeout = setTimeout(async () => {
      const provider = this.provider();
      const p = this.path();
      if (provider.saveNote) {
        try {
          const providerPath = p.length > 0 ? p.slice(1) : [];
          await provider.saveNote(providerPath, content);
        } catch (e) {
          console.error('Failed to save note:', e);
          // Optionally, show a toast to the user.
        }
      }
    }, 500); // Debounce saves by 500ms
  }

  onInput(event: Event): void {
    this.noteContent.set((event.target as HTMLTextAreaElement).value);
  }

  openInDialog(): void {
    const path = this.path();
    const title = path.length > 0 ? `Note for ${path[path.length - 1]}` : 'Note';
    this.textEditorService.open(this.noteContent, title, 'note.md');
  }

  applyMarkdown(prefix: string, suffix: string = prefix, placeholder: string = 'text'): void {
    if (!this.isNoteAvailable()) return;
    const textarea = this.editorTextarea?.nativeElement;
    if (!textarea) return;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const currentText = textarea.value;
    const selectedText = currentText.substring(start, end);

    let replacement = '';
    let selectionStartOffset = prefix.length;
    let selectionEndOffset = prefix.length;

    if (selectedText) {
      replacement = prefix + selectedText + suffix;
      selectionEndOffset += selectedText.length;
    } else {
      replacement = prefix + placeholder + suffix;
      selectionEndOffset += placeholder.length;
    }

    const newText = currentText.substring(0, start) + replacement + currentText.substring(end);
    this.noteContent.set(newText);
    
    // After render, focus and select the text
    setTimeout(() => {
        textarea.focus();
        textarea.setSelectionRange(start + selectionStartOffset, start + selectionEndOffset);
    }, 0);
  }

  addLink(): void {
    if (!this.isNoteAvailable()) return;
    const url = prompt('Enter URL:');
    if (url) {
      this.applyMarkdown('[', `](${url})`, 'link text');
    }
  }

  applyCode(): void {
    if (!this.isNoteAvailable()) return;
    const textarea = this.editorTextarea?.nativeElement;
    if (!textarea) return;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selectedText = textarea.value.substring(start, end);

    if (selectedText.includes('\n')) {
      // Block code
      this.applyMarkdown('```\n', '\n```', 'code');
    } else {
      // Inline code
      this.applyMarkdown('`', '`', 'code');
    }
  }
}
