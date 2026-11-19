import {
  Component,
  ChangeDetectionStrategy,
  input,
  output,
  signal,
  effect,
  ElementRef,
  ViewChild,
  AfterViewInit,
  OnDestroy,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';

// Monaco is loaded dynamically from CDN at runtime
declare const monaco: any;

@Component({
  selector: 'app-monaco-editor',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="flex flex-col h-full bg-[rgb(var(--color-background))]">
      <!-- Editor tabs bar -->
      <div class="flex items-center h-9 bg-[rgb(var(--color-surface-muted))] border-b border-[rgb(var(--color-border-base))] flex-shrink-0 overflow-x-auto">
        @for (tab of openFiles(); track tab.path; let i = $index) {
          <div
            class="flex items-center h-full px-3 text-sm border-r border-[rgb(var(--color-border-base))] cursor-pointer flex-shrink-0 max-w-[200px]"
            [class.bg-[rgb(var(--color-background))]]="tab.path === activeFile()"
            [class.border-b-2]="tab.path === activeFile()"
            [class.border-b-[rgb(var(--color-accent-ring))]]="tab.path === activeFile()"
            [class.text-[rgb(var(--color-accent-text))]]="tab.path === activeFile()"
            [class.text-[rgb(var(--color-text-muted))]]="tab.path !== activeFile()"
            (click)="selectTab(tab.path)"
          >
            <span class="truncate">{{ tab.name }}</span>
            @if (tab.dirty) {
              <span class="ml-2 w-2 h-2 rounded-full bg-[rgb(var(--color-accent-ring))]"></span>
            }
            <button
              class="ml-2 p-0.5 rounded hover:bg-[rgb(var(--color-surface-hover))] text-[rgb(var(--color-text-subtle))] hover:text-[rgb(var(--color-text-base))]"
              (click)="$event.stopPropagation(); closeTab(tab.path)"
            >
              <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        }
      </div>

      <!-- Monaco container -->
      <div #editorContainer class="flex-1 overflow-hidden"></div>

      <!-- Status bar -->
      <div class="flex items-center justify-between h-6 px-3 bg-[rgb(var(--color-surface-muted))] border-t border-[rgb(var(--color-border-base))] text-[10px] text-[rgb(var(--color-text-subtle))] flex-shrink-0">
        <span>{{ activeFile() || 'No file selected' }}</span>
        <span>{{ language() }}</span>
      </div>
    </div>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MonacoEditorComponent implements AfterViewInit, OnDestroy {
  @ViewChild('editorContainer', { static: true }) editorContainer!: ElementRef<HTMLDivElement>;

  openFiles = input<{ name: string; path: string; content: string; dirty: boolean }[]>([]);
  activeFile = input<string | null>(null);
  language = input('plaintext');

  tabSelected = output<string>();
  tabClosed = output<string>();
  contentChanged = output<{ path: string; content: string }>();
  editorReady = output<any>();

  private editor: any = null;
  private monacoLoaded = signal(false);
  private isUpdating = false;

  constructor() {
    // React to activeFile changes — update editor content and language
    effect(() => {
      const path = this.activeFile();
      const files = this.openFiles();
      if (!this.editor || !this.monacoLoaded()) return;
      this.isUpdating = true;
      const file = files.find(f => f.path === path);
      if (file) {
        const lang = this.detectLanguage(file.name);
        const model = this.editor.getModel();
        if (model) {
          monaco.editor.setModelLanguage(model, lang);
        }
        this.editor.setValue(file.content);
      } else {
        this.editor.setValue('');
      }
      this.isUpdating = false;
    });
  }

  ngAfterViewInit(): void {
    this.loadMonaco();
  }

  ngOnDestroy(): void {
    if (this.editor) {
      this.editor.dispose();
    }
  }

  private async loadMonaco(): Promise<void> {
    if (this.monacoLoaded()) return;
    try {
      // Load Monaco from CDN
      await this.injectMonacoScripts();
      this.monacoLoaded.set(true);
      this.createEditor();
    } catch (err) {
      console.error('Failed to load Monaco editor:', err);
    }
  }

  private injectMonacoScripts(): Promise<void> {
    return new Promise((resolve, reject) => {
      // Check if already loaded (via global monaco)
      if (typeof monaco !== 'undefined') {
        resolve();
        return;
      }
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs/loader.js';
      script.onload = () => {
        (window as any).require.config({
          paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs' },
        });
        (window as any).require(['vs/editor/editor.main'], () => {
          resolve();
        });
      };
      script.onerror = () => reject(new Error('Monaco script load failed'));
      document.head.appendChild(script);
    });
  }

  private createEditor(): void {
    if (!this.editorContainer?.nativeElement) return;
    if (this.editor) return;

    this.editor = monaco.editor.create(this.editorContainer.nativeElement, {
      value: '',
      language: this.language(),
      theme: 'vs-dark',
      automaticLayout: true,
      fontSize: 13,
      fontFamily: "'Fira Code', 'Cascadia Code', 'JetBrains Mono', monospace",
      minimap: { enabled: true },
      scrollBeyondLastLine: false,
      wordWrap: 'on',
      tabSize: 2,
      renderWhitespace: 'selection',
      bracketPairColorization: { enabled: true },
      guides: { bracketPairs: true },
    });

    this.editor.onDidChangeModelContent(() => {
      if (this.isUpdating || !this.activeFile()) return;
      this.contentChanged.emit({ path: this.activeFile()!, content: this.editor.getValue() });
    });

    this.editorReady.emit(this.editor);
    this.setContent();
  }

  private setContent(): void {
    if (!this.editor) return;
    const file = this.openFiles().find(f => f.path === this.activeFile());
    if (file) {
      const currentValue = this.editor.getValue();
      if (currentValue !== file.content) {
        this.editor.setValue(file.content);
      }
    } else {
      this.editor.setValue('');
    }
  }

  selectTab(path: string): void {
    this.tabSelected.emit(path);
  }

  closeTab(path: string): void {
    this.tabClosed.emit(path);
  }

  /** Call when activeFile or openFiles changes externally */
  refreshEditor(): void {
    if (!this.editor || !this.monacoLoaded()) return;
    const file = this.openFiles().find(f => f.path === this.activeFile());
    if (file) {
      const lang = this.detectLanguage(file.name);
      monaco.editor.setModelLanguage(this.editor.getModel(), lang);
    }
    this.setContent();
  }

  private detectLanguage(filename: string): string {
    const ext = filename.split('.').pop()?.toLowerCase() || '';
    const langMap: Record<string, string> = {
      ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
      json: 'json', html: 'html', css: 'css', scss: 'scss', less: 'less',
      md: 'markdown', py: 'python', rs: 'rust', go: 'go', java: 'java',
      xml: 'xml', yaml: 'yaml', yml: 'yaml', sql: 'sql', sh: 'shell',
      bash: 'shell', dockerfile: 'dockerfile', toml: 'ini', ini: 'ini',
      c: 'c', cpp: 'cpp', h: 'c', rb: 'ruby', php: 'php', swift: 'swift', kt: 'kotlin',
    };
    return langMap[ext] || 'plaintext';
  }
}
