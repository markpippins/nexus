import { Component, ChangeDetectionStrategy, input, output, signal, computed, effect, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FileSystemNode } from '../../models/file-system.model.js';
import { ImageService } from '../../services/image.service.js';
import { DragDropService, DragDropPayload } from '../../services/drag-drop.service.js';
import { NewBookmark } from '../../models/bookmark.model.js';
import { FileSystemProvider } from '../../services/file-system-provider.js';
import { FolderPropertiesService } from '../../services/folder-properties.service.js';

@Component({
  selector: 'app-tree-node',
  templateUrl: './tree-node.component.html',
  // FIX: For recursive components, the component itself must be included in the imports.
  // The self-import statement was removed from the top of the file to resolve a name conflict.
  imports: [CommonModule, TreeNodeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TreeNodeComponent implements OnInit {
  private dragDropService = inject(DragDropService);
  private folderPropertiesService = inject(FolderPropertiesService);

  node = input.required<FileSystemNode>();
  path = input.required<string[]>();
  currentPath = input.required<string[]>();
  level = input(0);
  expansionCommand = input<{ command: 'expand' | 'collapse', id: number } | null>();
  getImageService = input.required<(path: string[]) => ImageService>();
  getProvider = input.required<(path: string[]) => FileSystemProvider>();

  pathChange = output<string[]>();
  loadChildren = output<string[]>();
  itemsDropped = output<{ destPath: string[]; payload: DragDropPayload }>();
  bookmarkDropped = output<{ bookmark: NewBookmark, destPath: string[] }>();
  contextMenuRequest = output<{ event: MouseEvent; path: string[]; node: FileSystemNode }>();

  isExpanded = signal(false);
  imageHasError = signal(false);
  imageIsLoaded = signal(false);
  isDragOver = signal(false);

  properties = computed(() => this.folderPropertiesService.getProperties(this.path()));
  
  iconUrl = computed(() => {
    const service = this.getImageService()(this.path());
    const node = this.node();
    const props = this.properties();

    if (node.isServerRoot) {
        return service.getIconUrl({ ...node, name: 'cloud' });
    }
    return service.getIconUrl(node, props?.imageName);
  });

  isSelected = computed(() => {
    const p1 = this.path().join('/');
    const p2 = this.currentPath().join('/');
    return p1 === p2;
  });

  isExpandable = computed(() => {
    return this.node().type === 'folder';
  });

  displayName = computed(() => {
    const props = this.properties();
    if (props?.displayName) {
        return props.displayName;
    }
    return this.node().name;
  });

  folderChildren = computed(() => {
    const children = this.node().children;
    if (!children) {
      return [];
    }
    
    const folderChildren = children.filter(c => c.type === 'folder');

    // Special sorting for children of the root "Home" node.
    // The Home node is the only one that directly contains server roots.
    if (folderChildren.some(c => c.isServerRoot)) {
      const localNode = folderChildren.find(item => !item.isServerRoot);
      const serverNodes = folderChildren.filter(item => item.isServerRoot);
      
      serverNodes.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
      
      const result: FileSystemNode[] = [];
      if (localNode) {
        result.push(localNode);
      }
      result.push(...serverNodes);
      return result;
    }

    // Default alphabetical sort for all other nodes
    return folderChildren
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  });

  constructor() {
    // Effect for auto-expanding. It does NOT emit or load children.
    effect(() => {
      const currentStr = this.currentPath().join('/');
      const myPathStr = this.path().join('/');
      if (currentStr.startsWith(myPathStr) && currentStr !== myPathStr) {
        this.expandProgrammatically();
      }
    });

    // Effect for handling commands. It does NOT emit or load children.
    effect(() => {
      const command = this.expansionCommand();
      if (!command) return;

      if (command.command === 'expand') {
        this.expandProgrammatically();
      } else if (command.command === 'collapse') {
        if (this.level() > 0) {
          this.collapse();
        }
      }
    });

    effect(() => {
      // When the iconUrl changes, we need to reset the loading indicators.
      this.iconUrl(); // Establish dependency on the computed signal
      this.imageIsLoaded.set(false);
      this.imageHasError.set(false);
    });
  }

  ngOnInit(): void {
    // Expand the root node by default after initialization.
    if (this.level() === 0) {
      this.isExpanded.set(true);
    }
  }

  // This method only changes local state. SAFE to be called from effects.
  private expandProgrammatically(): void {
    const node = this.node();
    if (this.isExpandable() && !this.isExpanded()) {
      if (node.isServerRoot && !node.connected) {
        return;
      }
      this.isExpanded.set(true);
    }
  }

  private collapse(): void {
    if (this.isExpandable() && this.isExpanded()) {
      this.isExpanded.set(false);
    }
  }

  toggleExpand(event: MouseEvent): void {
    event.stopPropagation();
    const node = this.node();
    if (!this.isExpandable()) return;
    if (node.isServerRoot && !node.connected) {
        // Do not expand and do not try to load children if disconnected
        return;
    }

    const expanding = !this.isExpanded();
    this.isExpanded.set(expanding);

    if (expanding && !node.childrenLoaded) {
      this.loadChildren.emit(this.path());
    }
  }

  selectNode(): void {
    this.pathChange.emit(this.path());
  }

  onContextMenu(event: MouseEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.contextMenuRequest.emit({ event, path: this.path(), node: this.node() });
  }

  getChildPath(childNode: FileSystemNode): string[] {
    return [...this.path(), childNode.name];
  }

  onImageLoad(): void {
    this.imageIsLoaded.set(true);
  }

  onImageError(): void {
    this.imageHasError.set(true);
  }

  onDragOver(event: DragEvent): void {
    const payload = this.dragDropService.getPayload();
    if (!payload) return;
    
    if (payload.type === 'filesystem') {
        const { sourcePath, items } = payload.payload;
        if (items.some(item => this.path().join('/').startsWith([...sourcePath, item.name].join('/')))) {
            return;
        }
    }

    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = payload.type === 'filesystem' ? 'move' : 'copy';
    }
    this.isDragOver.set(true);
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(false);
    
    const payload = this.dragDropService.getPayload();
    if (!payload) return;

    const destPath = this.path();
    
    if (payload.type === 'filesystem') {
        this.itemsDropped.emit({ destPath, payload });
    } else if (payload.type === 'bookmark') {
        this.bookmarkDropped.emit({ bookmark: payload.payload.data, destPath });
    }
  }

  onDragStart(event: DragEvent): void {
    const provider = this.getProvider()(this.path());
    
    const payload: DragDropPayload = {
        type: 'filesystem',
        payload: { sourceProvider: provider, sourcePath: this.path().slice(0, -1), items: [this.node()] }
    };
    this.dragDropService.startDrag(payload);

    if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('application/json', JSON.stringify({ type: 'filesystem' }));
    }
  }

  // --- Child Event Bubbling ---
  onChildPathChange(path: string[]): void {
    this.pathChange.emit(path);
  }

  onLoadChildren(path: string[]): void {
    this.loadChildren.emit(path);
  }

  onChildItemsDropped(event: { destPath: string[]; payload: DragDropPayload }): void {
    this.itemsDropped.emit(event);
  }

  onChildBookmarkDropped(event: { bookmark: NewBookmark, destPath: string[] }): void {
    this.bookmarkDropped.emit(event);
  }

  onChildContextMenuRequest(event: { event: MouseEvent; path: string[]; node: FileSystemNode; }): void {
    this.contextMenuRequest.emit(event);
  }
}
