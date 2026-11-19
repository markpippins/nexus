import { Component, ChangeDetectionStrategy, input, output, inject, computed, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FileSystemNode } from '../../models/file-system.model.js';
import { ImageService } from '../../services/image.service.js';
import { FolderPropertiesService } from '../../services/folder-properties.service.js';
import { FolderProperties } from '../../models/folder-properties.model.js';

@Component({
  selector: 'app-properties-dialog',
  templateUrl: './properties-dialog.component.html',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PropertiesDialogComponent implements OnInit {
  item = input.required<FileSystemNode>();
  parentPath = input.required<string[]>();
  imageService = input.required<ImageService>();
  
  close = output<void>();
  save = output<Partial<Omit<FolderProperties, 'path'>>>();

  private folderPropertiesService = inject(FolderPropertiesService);

  formState = signal({ displayName: '', imageName: '' });
  
  fullPath = computed(() => [...this.parentPath(), this.item().name]);
  
  displayName = computed(() => {
    const props = this.folderPropertiesService.getProperties(this.fullPath());
    if (props?.displayName) {
      return props.displayName;
    }
    return this.item().name;
  });

  ngOnInit(): void {
    if (this.item().type === 'folder') {
      const props = this.folderPropertiesService.getProperties(this.fullPath());
      this.formState.set({
        displayName: props?.displayName ?? '',
        imageName: props?.imageName ?? ''
      });
    }
  }

  getIconUrl(item: FileSystemNode): string | null {
    if (this.item().type === 'folder') {
      const imageName = this.formState().imageName;
      return this.imageService().getIconUrl(item, imageName);
    }
    return this.imageService().getIconUrl(item);
  }
  
  onValueChange(event: Event, field: 'displayName' | 'imageName'): void {
    const value = (event.target as HTMLInputElement).value;
    this.formState.update(state => ({ ...state, [field]: value }));
  }

  onSave(): void {
    if (this.item().type === 'folder') {
      this.save.emit(this.formState());
    } else {
      this.close.emit();
    }
  }
}
