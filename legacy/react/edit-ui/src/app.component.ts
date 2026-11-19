import { Component, ChangeDetectionStrategy, signal, computed, inject, effect, HostListener, OnInit, OnDestroy, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { FileTreeComponent } from './components/file-tree/file-tree.component';
import { MonacoEditorComponent } from './components/monaco-editor/monaco-editor.component';
import { UiPreferencesService, FileTreeNode } from './services/ui-preferences.service';

interface OpenFile {
  name: string;
  path: string;
  content: string;
  dirty: boolean;
}

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  imports: [CommonModule, FileTreeComponent, MonacoEditorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent implements OnInit, OnDestroy {
  private prefs = inject(UiPreferencesService);
  private http = inject(HttpClient);
  private ngZone = inject(NgZone);

  // Theme
  currentTheme = this.prefs.theme;
  fileTreeWidth = signal(this.prefs.fileTreeWidth());
  isResizingTree = signal(false);
  private treeResizeCleanup: (() => void) | null = null;

  // Address bar
  currentPath = signal('');
  addressSegments = computed(() => {
    const p = this.currentPath();
    if (!p) return [];
    return p.split('/').filter(Boolean);
  });

  // File tree
  fileTreeNodes = signal<FileTreeNode[]>([]);
  fileTreeLoading = signal(false);

  // Editor
  openFiles = signal<OpenFile[]>([]);
  activeFilePath = signal<string | null>(null);
  contentChangeCounter = signal(0);

  // API base URL — the file-system-server on port 4042 (edit-ui consumer)
  apiBaseUrl = signal('http://localhost:4042');

  constructor() {
    // Apply theme to body
    effect(() => {
      const theme = this.currentTheme();
      document.body.classList.remove('theme-light', 'theme-steel', 'theme-dark');
      document.body.classList.add(theme);
    });
  }

  ngOnInit(): void {
    // Load file tree on startup
    this.loadFileTree();
  }

  // --- Theme ---
  // (theme is applied via body class in constructor; toggle removed)

  // --- File Tree Resize ---
  startTreeResize(event: MouseEvent): void {
    this.isResizingTree.set(true);
    event.preventDefault();
    const startX = event.clientX;
    const initialWidth = this.fileTreeWidth();
    const prevUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = 'none';

    this.ngZone.runOutsideAngular(() => {
      const onMove = (e: MouseEvent) => {
        let newWidth = initialWidth + (e.clientX - startX);
        if (newWidth < 160) newWidth = 160;
        if (newWidth > 600) newWidth = 600;
        this.fileTreeWidth.set(newWidth);
      };

      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        document.body.style.userSelect = prevUserSelect;
        this.treeResizeCleanup = null;
        this.ngZone.run(() => {
          this.isResizingTree.set(false);
          this.prefs.setFileTreeWidth(this.fileTreeWidth());
        });
      };

      this.treeResizeCleanup = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        document.body.style.userSelect = prevUserSelect;
      };

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  ngOnDestroy(): void {
    this.treeResizeCleanup?.();
  }

  // --- File Tree ---
  loadFileTree(path?: string): void {
    this.fileTreeLoading.set(true);
    const dirPath = path || '';
    const url = `${this.apiBaseUrl()}/api/fs${dirPath ? `?path=${encodeURIComponent(dirPath)}` : ''}`;

    this.http.get<{ entries: any[] }>(url).subscribe({
      next: (data) => {
        const nodes: FileTreeNode[] = (data.entries || []).map((e: any) => ({
          name: e.name,
          path: e.path,
          type: e.type || 'file',
          children: undefined,
          childrenLoaded: false,
        }));
        this.fileTreeNodes.set(nodes);
        this.fileTreeLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to load file tree:', err);
        this.fileTreeLoading.set(false);
        this.fileTreeNodes.set([]);
      },
    });
  }

  /** Lazy-load children for a directory node when it's first expanded */
  loadDirectoryChildren(node: FileTreeNode): void {
    if (node.type !== 'directory' || node.childrenLoaded) return;

    const url = `${this.apiBaseUrl()}/api/fs?path=${encodeURIComponent(node.path)}`;
    this.http.get<{ entries: any[] }>(url).subscribe({
      next: (data) => {
        const children: FileTreeNode[] = (data.entries || []).map((e: any) => ({
          name: e.name,
          path: e.path,
          type: e.type || 'file',
          children: undefined,
          childrenLoaded: false,
        }));
        node.children = children;
        node.childrenLoaded = true;
        // Trigger change detection by replacing the nodes signal
        this.fileTreeNodes.set([...this.fileTreeNodes()]);
      },
      error: (err) => {
        console.error('Failed to load children:', err);
        node.children = [];
        node.childrenLoaded = true;
        this.fileTreeNodes.set([...this.fileTreeNodes()]);
      },
    });
  }

  onFileTreeSelect(path: string): void {
    this.currentPath.set(path);
    this.openFile(path);
  }

  onFileTreeRefresh(): void {
    this.loadFileTree(this.currentPath());
  }

  // --- File Operations ---
  openFile(path: string): void {
    // Check if already open
    const existing = this.openFiles().find(f => f.path === path);
    if (existing) {
      this.activeFilePath.set(path);
      return;
    }

    // Fetch file content
    const url = `${this.apiBaseUrl()}/api/fs/content?path=${encodeURIComponent(path)}`;
    this.http.get<{ content: string }>(url).subscribe({
      next: (data) => {
        const name = path.split('/').pop() || path;
        this.openFiles.update(files => [...files, { name, path, content: data.content || '', dirty: false }]);
        this.activeFilePath.set(path);
      },
      error: (err) => {
        console.error('Failed to load file content:', err);
        // Open anyway with empty content
        const name = path.split('/').pop() || path;
        this.openFiles.update(files => [...files, { name, path, content: '', dirty: false }]);
        this.activeFilePath.set(path);
      },
    });
  }

  onContentChanged(event: { path: string; content: string }): void {
    this.openFiles.update(files =>
      files.map(f => (f.path === event.path ? { ...f, content: event.content, dirty: true } : f))
    );
  }

  closeFile(path: string): void {
    const wasActive = this.activeFilePath() === path;
    const remaining = this.openFiles().filter(f => f.path !== path);

    if (wasActive) {
      if (remaining.length > 0) {
        const closedIdx = this.openFiles().findIndex(f => f.path === path);
        const newIdx = Math.min(closedIdx, remaining.length - 1);
        this.activeFilePath.set(remaining[newIdx].path);
      } else {
        this.activeFilePath.set(null);
      }
    }
    this.openFiles.set(remaining);
  }

  selectTab(path: string): void {
    this.activeFilePath.set(path);
    this.currentPath.set(path);
  }

  // --- Address Bar ---
  navigateToSegment(index: number): void {
    const segments = this.addressSegments();
    const newPath = '/' + segments.slice(0, index + 1).join('/');
    this.currentPath.set(newPath);
    this.loadFileTree(newPath);
  }

  goUp(): void {
    const segments = this.addressSegments();
    if (segments.length <= 1) {
      this.currentPath.set('');
      this.loadFileTree('');
    } else {
      const newPath = '/' + segments.slice(0, -1).join('/');
      this.currentPath.set(newPath);
      this.loadFileTree(newPath);
    }
  }

  // --- Keyboard Shortcuts ---
  @HostListener('document:keydown', ['$event'])
  onKeyDown(event: KeyboardEvent): void {
    // Ctrl+S: Save current file
    if ((event.ctrlKey || event.metaKey) && event.key === 's') {
      event.preventDefault();
      this.saveCurrentFile();
    }
    // Ctrl+W: Close current tab
    if ((event.ctrlKey || event.metaKey) && event.key === 'w') {
      event.preventDefault();
      const active = this.activeFilePath();
      if (active) this.closeFile(active);
    }
    // F2: Rename
    if (event.key === 'F2') {
      event.preventDefault();
      this.renameSelected();
    }
    // Delete: Delete selected file
    if (event.key === 'Delete' && this.activeFilePath()) {
      event.preventDefault();
      this.deleteSelected();
    }
  }

  // --- File CRUD Operations ---

  /** Create a new empty file in the current directory */
  createNewFile(): void {
    const name = window.prompt('File name:');
    if (!name || !name.trim()) return;

    const dirPath = this.currentPath() || '';
    const filePath = dirPath ? `${dirPath}/${name.trim()}` : name.trim();

    this.http.put(`${this.apiBaseUrl()}/api/fs/content?path=${encodeURIComponent(filePath)}`, { content: '' })
      .subscribe({
        next: () => {
          this.onFileTreeRefresh();
          this.openFile(filePath);
        },
        error: (err) => console.error('Failed to create file:', err),
      });
  }

  /** Create a new folder in the current directory */
  createNewFolder(): void {
    const name = window.prompt('Folder name:');
    if (!name || !name.trim()) return;

    const dirPath = this.currentPath() || '';
    const pathParts = dirPath ? dirPath.split('/').filter(Boolean) : [];

    this.http.post(`${this.apiBaseUrl()}/fs`, {
      operation: 'mkdir',
      path: [...pathParts, name.trim()],
    }).subscribe({
      next: () => {
        this.onFileTreeRefresh();
      },
      error: (err) => console.error('Failed to create folder:', err),
    });
  }

  /** Rename the currently selected file */
  renameSelected(): void {
    const active = this.activeFilePath();
    if (!active) return;

    const oldName = active.split('/').pop() || active;
    const newName = window.prompt('Rename to:', oldName);
    if (!newName || !newName.trim() || newName.trim() === oldName) return;

    // rename expects path as the FULL path including the filename
    const fullPath = active.split('/').filter(Boolean);

    this.http.post(`${this.apiBaseUrl()}/fs`, {
      operation: 'rename',
      path: fullPath,
      newName: newName.trim(),
    }).subscribe({
      next: () => {
        const parentParts = fullPath.slice(0, -1);
        const newPath = parentParts.length > 0 ? `${parentParts.join('/')}/${newName.trim()}` : newName.trim();
        // Update open tabs
        this.openFiles.update(files =>
          files.map(f => f.path === active ? { ...f, name: newName.trim(), path: newPath } : f)
        );
        this.activeFilePath.set(newPath);
        this.currentPath.set(newPath);
        this.onFileTreeRefresh();
      },
      error: (err) => console.error('Failed to rename:', err),
    });
  }

  /** Delete the currently selected file */
  deleteSelected(): void {
    const active = this.activeFilePath();
    if (!active) return;

    const name = active.split('/').pop() || active;
    if (!window.confirm(`Delete "${name}"?`)) return;

    const parentPath = active.split('/').slice(0, -1);

    this.http.post(`${this.apiBaseUrl()}/fs`, {
      operation: 'deletefile',
      path: parentPath,
      filename: name,
    }).subscribe({
      next: () => {
        this.closeFile(active);
        this.onFileTreeRefresh();
      },
      error: (err) => console.error('Failed to delete:', err),
    });
  }

  saveCurrentFile(): void {
    const active = this.activeFilePath();
    if (!active) return;
    const file = this.openFiles().find(f => f.path === active);
    if (!file || !file.dirty) return;

    const url = `${this.apiBaseUrl()}/api/fs/content?path=${encodeURIComponent(active)}`;
    this.http.put(url, { content: file.content }).subscribe({
      next: () => {
        this.openFiles.update(files =>
          files.map(f => (f.path === active ? { ...f, dirty: false } : f))
        );
      },
      error: (err) => console.error('Failed to save file:', err),
    });
  }

  // --- API base URL management ---
  setApiBaseUrl(url: string): void {
    this.apiBaseUrl.set(url);
    this.loadFileTree();
  }
}
