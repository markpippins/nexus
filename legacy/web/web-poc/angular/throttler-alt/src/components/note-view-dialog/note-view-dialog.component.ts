import { Component, ChangeDetectionStrategy, input, output, signal, computed, ViewChild, ElementRef, WritableSignal } from '@angular/core';
import { CommonModule } from '@angular/common';

// Declare the globals from the CDN scripts
declare var marked: { parse(markdown: string): string; };
declare var DOMPurify: { sanitize(dirty: string): string; };

@Component({
  selector: 'app-text-editor-dialog',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './note-view-dialog.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TextEditorDialogComponent {
  contentSignal = input.required<WritableSignal<string>>();
  title = input.required<string>();
  fileName = input.required<string>();
  close = output<void>();
  
  mode = signal<'edit' | 'preview'>('edit');

  @ViewChild('editor') editorTextarea: ElementRef<HTMLTextAreaElement> | undefined;

  isMarkdown = computed(() => {
    return this.fileName().toLowerCase().endsWith('.md');
  });

  renderedHtml = computed(() => {
    const content = this.contentSignal()();
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        const rawHtml = marked.parse(content);
        return DOMPurify.sanitize(rawHtml);
    }
    return '<p>Error: Markdown parsing libraries not loaded.</p>';
  });

  onInput(event: Event): void {
    this.contentSignal().set((event.target as HTMLTextAreaElement).value);
  }
  
  applyMarkdown(prefix: string, suffix: string = prefix, placeholder: string = 'text'): void {
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
    this.contentSignal().set(newText);
    
    // After render, focus and select the text
    setTimeout(() => {
        textarea.focus();
        textarea.setSelectionRange(start + selectionStartOffset, start + selectionEndOffset);
    }, 0);
  }

  addLink(): void {
    const url = prompt('Enter URL:');
    if (url) {
      this.applyMarkdown('[', `](${url})`, 'link text');
    }
  }

  applyCode(): void {
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
