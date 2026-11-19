import { Component, ChangeDetectionStrategy, signal, inject, Renderer2, OnDestroy, input, output, HostListener, ElementRef, computed, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ChatComponent } from '../chat/chat.component.js';
import { FileSystemNode } from '../../models/file-system.model.js';
import { TreeViewComponent } from '../tree-view/tree-view.component.js';
import { ImageService } from '../../services/image.service.js';
import { DragDropPayload } from '../../services/drag-drop.service.js';
import { NewBookmark } from '../../models/bookmark.model.js';
import { InputDialogComponent } from '../input-dialog/input-dialog.component.js';
import { ConfirmDialogComponent } from '../confirm-dialog/confirm-dialog.component.js';
import { FileSystemProvider } from '../../services/file-system-provider.js';
import { UiPreferencesService } from '../../services/ui-preferences.service.js';
import { ExportDialogComponent } from '../export-dialog/export-dialog.component.js';
import { ImportDialogComponent } from '../import-dialog/import-dialog.component.js';
import { NotesComponent } from '../notes/notes.component.js';

@Component({
  selector: 'app-sidebar',
  templateUrl: './sidebar.component.html',
  imports: [CommonModule, ChatComponent, TreeViewComponent, InputDialogComponent, ConfirmDialogComponent, ExportDialogComponent, ImportDialogComponent, NotesComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SidebarComponent implements OnDestroy {
  private renderer = inject(Renderer2);
  private elementRef = inject(ElementRef);
  private uiPreferencesService = inject(UiPreferencesService);

  folderTree = input<FileSystemNode | null>(null);
  currentPath = input.required<string[]>();
  getImageService = input.required<(path: string[]) => ImageService>();
  getProvider = input.required<(path: string[]) => FileSystemProvider>();
  isTreeVisible = input(true);
  isChatVisible = input(true);
  isNotesVisible = input(true);
  
  pathChange = output<string[]>();
  refreshTree = output<void>();
  loadChildren = output<string[]>();
  itemsMoved = output<{ destPath: string[]; payload: DragDropPayload }>();
  bookmarkDropped = output<{ bookmark: NewBookmark, destPath: string[] }>();
  serversMenuClick = output<void>();
  localConfigMenuClick = output<void>();
  importClick = output<void>();
  exportClick = output<void>();
  renameItemInTree = output<{ path: string[], newName: string }>();
  deleteItemInTree = output<string[]>();
  createFolderInTree = output<{ path: string[], name: string }>();
  createFileInTree = output<{ path: string[], name: string }>();
  connectToServer = output<string>();
  disconnectFromServer = output<string>();
  editServerProfile = output<string>();

  width = signal(this.uiPreferencesService.sidebarWidth() ?? 288);
  isResizing = signal(false);
  treeExpansionCommand = signal<{ command: 'expand' | 'collapse', id: number } | null>(null);
  isHamburgerMenuOpen = signal(false);

  private unlistenMouseMove: (() => void) | null = null;
  private unlistenMouseUp: (() => void) | null = null;

  // --- Vertical Resizing State for internal panes ---
  treePaneHeight = signal(this.uiPreferencesService.sidebarTreeHeight() ?? 400);
  chatPaneHeight = signal(this.uiPreferencesService.sidebarChatHeight() ?? 250);
  
  isResizingTree = signal(false);
  isResizingChat = signal(false);

  private unlistenTreeMouseMove: (() => void) | null = null;
  private unlistenTreeMouseUp: (() => void) | null = null;
  private unlistenChatMouseMove: (() => void) | null = null;
  private unlistenChatMouseUp: (() => void) | null = null;
  
  isChatPaneCollapsed = signal(false);
  isNotesPaneCollapsed = signal(false);

  @ViewChild('contentContainer') contentContainerEl!: ElementRef<HTMLDivElement>;
  
  isTreeFlex = computed(() => {
    const chatIsEffectivelyHidden = !this.isChatVisible() || this.isChatPaneCollapsed();
    const notesIsEffectivelyHidden = !this.isNotesVisible() || this.isNotesPaneCollapsed();
    return chatIsEffectivelyHidden && notesIsEffectivelyHidden;
  });

  // --- Context Menu State ---
  contextMenu = signal<{ x: number; y: number; path: string[]; node: FileSystemNode } | null>(null);
  
  // --- Dialog State ---
  isInputDialogOpen = signal(false);
  private inputDialogCallback = signal<((value: string) => void) | null>(null);
  inputDialogConfig = signal<{ title: string; message: string; initialValue: string }>({ title: '', message: '', initialValue: '' });
  
  isConfirmDialogOpen = signal(false);
  private confirmDialogCallback = signal<(() => void) | null>(null);
  confirmDialogConfig = signal<{ title: string; message: string; confirmText: string }>({ title: '', message: '', confirmText: 'OK' });
  
  currentProvider = computed(() => this.getProvider()(this.currentPath()));

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: Event) {
    if (!this.elementRef.nativeElement.contains(event.target)) {
        if (this.isHamburgerMenuOpen()) this.isHamburgerMenuOpen.set(false);
    }
    // Always close context menu on any document click
    if (this.contextMenu()) this.contextMenu.set(null);
  }
  
  startResize(event: MouseEvent): void {
    this.isResizing.set(true);
    const startX = event.clientX;
    const startWidth = this.width();

    event.preventDefault();

    this.unlistenMouseMove = this.renderer.listen('document', 'mousemove', (e: MouseEvent) => {
      const dx = e.clientX - startX;
      let newWidth = startWidth + dx;
      
      if (newWidth < 150) newWidth = 150;
      if (newWidth > 500) newWidth = 500;
      
      this.width.set(newWidth);
    });

    this.unlistenMouseUp = this.renderer.listen('document', 'mouseup', () => {
      this.stopResize();
    });
  }

  private stopResize(): void {
    if (!this.isResizing()) return;
    this.isResizing.set(false);
    if (this.unlistenMouseMove) {
      this.unlistenMouseMove();
      this.unlistenMouseMove = null;
    }
    if (this.unlistenMouseUp) {
      this.unlistenMouseUp();
      this.unlistenMouseUp = null;
    }
    this.uiPreferencesService.setSidebarWidth(this.width());
  }

  // --- Vertical resize methods ---
  startTreeResize(event: MouseEvent): void {
    this.isResizingTree.set(true);
    const container = this.contentContainerEl.nativeElement;
    const containerRect = container.getBoundingClientRect();
    event.preventDefault();

    this.unlistenTreeMouseMove = this.renderer.listen('document', 'mousemove', (e: MouseEvent) => {
      let newTreeHeight = e.clientY - containerRect.top;
      
      const minHeight = 100;
      
      let occupiedByOtherPanes = 0;
      if (this.isChatVisible()) {
        if (this.isChatPaneCollapsed()) {
          // Collapsed chat pane is just its header height (h-12 = 3rem = 48px)
          occupiedByOtherPanes += 48;
        } else {
          occupiedByOtherPanes += this.chatPaneHeight();
        }
      }

      if (this.isNotesVisible() && !this.isNotesPaneCollapsed()) {
        // Leave at least 100px for the notes pane
        occupiedByOtherPanes += 100;
      }
      
      const maxHeight = containerRect.height - occupiedByOtherPanes;

      if (newTreeHeight < minHeight) newTreeHeight = minHeight;
      if (newTreeHeight > maxHeight) newTreeHeight = maxHeight;
      
      this.treePaneHeight.set(newTreeHeight);
    });

    this.unlistenTreeMouseUp = this.renderer.listen('document', 'mouseup', () => {
      this.stopTreeResize();
    });
  }

  private stopTreeResize(): void {
    if (!this.isResizingTree()) return;
    this.isResizingTree.set(false);
    if (this.unlistenTreeMouseMove) {
      this.unlistenTreeMouseMove();
      this.unlistenTreeMouseMove = null;
    }
    if (this.unlistenTreeMouseUp) {
      this.unlistenTreeMouseUp();
      this.unlistenTreeMouseUp = null;
    }
    this.uiPreferencesService.setSidebarTreeHeight(this.treePaneHeight());
  }

  startChatResize(event: MouseEvent): void {
    this.isResizingChat.set(true);
    const container = this.contentContainerEl.nativeElement;
    const containerRect = container.getBoundingClientRect();
    event.preventDefault();

    this.unlistenChatMouseMove = this.renderer.listen('document', 'mousemove', (e: MouseEvent) => {
      const totalHeightFromTop = e.clientY - containerRect.top;
      let newHeight = totalHeightFromTop - this.treePaneHeight();

      const minHeight = 100;
      const notesMinHeight = (this.isNotesVisible() && !this.isNotesPaneCollapsed()) ? 100 : 0;
      const maxHeight = containerRect.height - this.treePaneHeight() - notesMinHeight;

      if (newHeight < minHeight) newHeight = minHeight;
      if (newHeight > maxHeight) newHeight = maxHeight;
      
      this.chatPaneHeight.set(newHeight);
    });

    this.unlistenChatMouseUp = this.renderer.listen('document', 'mouseup', () => {
      this.stopChatResize();
    });
  }

  private stopChatResize(): void {
    if (!this.isResizingChat()) return;
    this.isResizingChat.set(false);
    if (this.unlistenChatMouseMove) {
      this.unlistenChatMouseMove();
      this.unlistenChatMouseMove = null;
    }
    if (this.unlistenChatMouseUp) {
      this.unlistenChatMouseUp();
      this.unlistenChatMouseUp = null;
    }
    this.uiPreferencesService.setSidebarChatHeight(this.chatPaneHeight());
  }

  toggleChatPaneCollapse(): void {
    this.isChatPaneCollapsed.update(v => !v);
  }
  
  toggleNotesPaneCollapse(): void {
    this.isNotesPaneCollapsed.update(v => !v);
  }

  onTreeViewPathChange(path: string[]): void {
    this.pathChange.emit(path);
  }

  onRefreshTree(): void {
    this.refreshTree.emit();
  }

  onExpandAll(): void {
    this.treeExpansionCommand.set({ command: 'expand', id: Date.now() });
  }

  onCollapseAll(): void {
    this.treeExpansionCommand.set({ command: 'collapse', id: Date.now() });
  }

  onLoadChildren(path: string[]): void {
    this.loadChildren.emit(path);
  }

  onItemsDropped(event: { destPath: string[]; payload: DragDropPayload }): void {
    this.itemsMoved.emit(event);
  }

  onBookmarkDropped(event: { bookmark: NewBookmark, destPath: string[] }): void {
    this.bookmarkDropped.emit(event);
  }
  
  toggleHamburgerMenu(event: MouseEvent): void {
    event.stopPropagation();
    this.isHamburgerMenuOpen.update(v => !v);
  }

  onMenuItemClick(emitterName: 'localConfigMenuClick' | 'importClick' | 'exportClick' | 'serversMenuClick'): void {
    switch (emitterName) {
      case 'localConfigMenuClick':
        this.localConfigMenuClick.emit();
        break;
      case 'importClick':
        this.importClick.emit();
        break;
      case 'exportClick':
        this.exportClick.emit();
        break;
      case 'serversMenuClick':
        this.serversMenuClick.emit();
        break;
    }
    this.isHamburgerMenuOpen.set(false);
  }
  
  onTreeContextMenu(event: { event: MouseEvent; path: string[]; node: FileSystemNode; }): void {
    event.event.preventDefault();
    event.event.stopPropagation();
    this.contextMenu.set({ x: event.event.clientX, y: event.event.clientY, path: event.path, node: event.node });
  }

  private findNodeByPath(root: FileSystemNode | null, path: string[]): FileSystemNode | null {
    if (!root) return null;

    if (path.length === 0) {
      // An empty path corresponds to the root of the tree itself ('Home')
      return root;
    }

    let currentNode: FileSystemNode | undefined = root;
    for (const segment of path) {
      currentNode = currentNode?.children?.find(c => c.name === segment);
      if (!currentNode) return null;
    }
    
    return currentNode ?? null;
  }
  
  onSidebarAreaContextMenu(event: MouseEvent): void {
    event.preventDefault();
    event.stopPropagation();
    
    this.contextMenu.set(null); // Close any existing menu

    const path = this.currentPath();
    const root = this.folderTree();
    const nodeForPath = this.findNodeByPath(root, path);

    if (nodeForPath) {
      // Set the context to the currently active path, allowing actions like "New Folder"
      // to apply to the folder currently being viewed in the main pane.
      this.contextMenu.set({ x: event.clientX, y: event.clientY, path: path, node: nodeForPath });
    }
  }

  isChildOfWritableRoot(path: string[]): boolean {
    if (path.length <= 1) {
      // This is a safeguard. The template logic should prevent this from being called
      // for the Home root (path.length === 0). For top-level items, we let the
      // specific menu logic handle it.
      return false;
    }

    const rootNodeName = path[0];
    // The folderTree is the 'Home' node. Its children are the roots we care about.
    const rootNodeInTree = this.folderTree()?.children?.find(c => c.name === rootNodeName);

    if (rootNodeInTree?.isServerRoot) {
      // It's a child of a server. Writable only if connected.
      return rootNodeInTree.connected ?? false;
    }
    
    // If not a child of a server root, it must be a child of the local session root,
    // which is always writable.
    return true;
  }

  // --- Context Menu Action Handlers ---
  handleSettings(): void {
    this.localConfigMenuClick.emit();
    this.contextMenu.set(null);
  }

  handleRename(): void {
    const ctx = this.contextMenu();
    if (!ctx) return;
    this.contextMenu.set(null);
    this.inputDialogConfig.set({ title: 'Rename', message: 'Enter a new name:', initialValue: ctx.node.name });
    this.inputDialogCallback.set((newName: string) => {
      this.renameItemInTree.emit({ path: ctx.path, newName });
    });
    this.isInputDialogOpen.set(true);
  }

  handleDelete(): void {
    const ctx = this.contextMenu();
    if (!ctx) return;
    this.contextMenu.set(null);
    this.confirmDialogConfig.set({ title: 'Confirm Deletion', message: `Are you sure you want to delete "${ctx.node.name}"?`, confirmText: 'Delete' });
    this.confirmDialogCallback.set(() => {
        this.deleteItemInTree.emit(ctx.path);
    });
    this.isConfirmDialogOpen.set(true);
  }

  handleNewFolder(): void {
    const ctx = this.contextMenu();
    if (!ctx) return;
    this.contextMenu.set(null);
    this.inputDialogConfig.set({ title: 'New Folder', message: `Enter a name for the new folder inside "${ctx.node.name}":`, initialValue: 'New folder' });
    this.inputDialogCallback.set((name: string) => {
      this.createFolderInTree.emit({ path: ctx.path, name });
    });
    this.isInputDialogOpen.set(true);
  }

  handleNewFile(): void {
    const ctx = this.contextMenu();
    if (!ctx) return;
    this.contextMenu.set(null);
    this.inputDialogConfig.set({ title: 'New File', message: `Enter a name for the new file inside "${ctx.node.name}":`, initialValue: 'New file.txt' });
    this.inputDialogCallback.set((name: string) => {
      this.createFileInTree.emit({ path: ctx.path, name });
    });
    this.isInputDialogOpen.set(true);
  }
  
  handleConnect(): void {
    const profileId = this.contextMenu()?.node.profileId;
    if (profileId) {
      this.connectToServer.emit(profileId);
    }
    this.contextMenu.set(null);
  }

  handleDisconnect(): void {
    const profileId = this.contextMenu()?.node.profileId;
    if (profileId) {
      this.disconnectFromServer.emit(profileId);
    }
    this.contextMenu.set(null);
  }

  handleEditProfile(): void {
    const profileId = this.contextMenu()?.node.profileId;
    if (profileId) {
      this.editServerProfile.emit(profileId);
    }
    this.contextMenu.set(null);
  }

  // --- Dialog Submit/Cancel Handlers ---

  onInputDialogSubmit(value: string): void {
    this.inputDialogCallback()?.(value);
    this.isInputDialogOpen.set(false);
  }
  
  onConfirmDialogConfirm(): void {
    this.confirmDialogCallback()?.();
    this.isConfirmDialogOpen.set(false);
  }

  ngOnDestroy(): void {
    this.stopResize();
    this.stopTreeResize();
    this.stopChatResize();
  }
}
