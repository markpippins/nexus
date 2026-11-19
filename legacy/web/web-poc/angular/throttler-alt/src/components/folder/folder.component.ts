import { Component, ChangeDetectionStrategy, input, output, signal, effect, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FileSystemNode } from '../../models/file-system.model';
import { AutoFocusSelectDirective } from '../../directives/auto-focus-select.directive.js';
import { DragDropService } from '../../services/drag-drop.service.js';
import { NewBookmark } from '../../models/bookmark.model.js';

@Component({
  selector: 'app-folder',
  templateUrl: './folder.component.html',
  imports: [CommonModule, AutoFocusSelectDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FolderComponent {
  item = input.required<FileSystemNode>();
  displayName = input.required<string>();
  iconUrl = input<string | null>(null);
  hasFailedToLoadImage = input<boolean>(false);
  isSelected = input<boolean>(false);
  isEditing = input(false);
  isDragOver = signal(false);

  itemContextMenu = output<{ event: MouseEvent; item: FileSystemNode }>();
  itemDrop = output<{ files: FileList; item: FileSystemNode }>();
  internalItemDrop = output<{ dropOn: FileSystemNode }>();
  bookmarkDropped = output<{ bookmark: NewBookmark, dropOn: FileSystemNode }>();
  imageError = output<string>();
  rename = output<string>();
  cancelRename = output<void>();

  imageIsLoaded = signal(false);
  private dragDropService = inject(DragDropService);

  constructor() {
    effect(() => {
      // Reset loaded state when the iconUrl input changes
      this.iconUrl();
      this.imageIsLoaded.set(false);
    });
  }

  onContextMenu(event: MouseEvent): void {
    // Prevent context menu while editing
    if (this.isEditing()) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    this.itemContextMenu.emit({ event, item: this.item() });
  }

  onImageLoad(): void {
    this.imageIsLoaded.set(true);
  }

  onImageError(): void {
    this.imageError.emit(this.item().name);
  }

  onDragOver(event: DragEvent): void {
    event.stopPropagation();
    const payload = this.dragDropService.getPayload();
    const isFileDrag = event.dataTransfer?.types.includes('Files');

    if (!payload && !isFileDrag) return;

    // For internal drags, prevent dropping on the item being dragged
    if (payload?.type === 'filesystem') {
      // Can't drop on self or on one of the other selected items being dragged
      if (payload.payload.items.some(i => i.name === this.item().name)) {
        return; // Invalid target, do not preventDefault
      }
    }
    
    event.preventDefault(); // Allow drop
    if (event.dataTransfer) {
      if (payload?.type === 'filesystem') {
        event.dataTransfer.dropEffect = 'move';
      } else {
        event.dataTransfer.dropEffect = 'copy';
      }
    }
    this.isDragOver.set(true);
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(false);

    const payload = this.dragDropService.getPayload();
    if (payload?.type === 'filesystem') {
      this.internalItemDrop.emit({ dropOn: this.item() });
      return;
    }

    if (payload?.type === 'bookmark') {
      this.bookmarkDropped.emit({ bookmark: payload.payload.data, dropOn: this.item() });
      return;
    }

    const files = event.dataTransfer?.files;
    if (files && files.length > 0) {
      this.itemDrop.emit({ files, item: this.item() });
    }
  }
}
