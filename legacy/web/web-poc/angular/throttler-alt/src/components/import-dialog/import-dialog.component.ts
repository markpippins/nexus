import { Component, ChangeDetectionStrategy, input, output, signal, ViewChild, ElementRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FileSystemNode } from '../../models/file-system.model.js';
import { ImageService } from '../../services/image.service.js';
import { FileSystemProvider } from '../../services/file-system-provider.js';
import { TreeViewComponent } from '../tree-view/tree-view.component.js';

@Component({
  selector: 'app-import-dialog',
  standalone: true,
  imports: [CommonModule, TreeViewComponent],
  templateUrl: './import-dialog.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ImportDialogComponent {
  localSessionNode = input.required<FileSystemNode | null>();
  getImageService = input.required<(path: string[]) => ImageService>();
  getProvider = input.required<(path: string[]) => FileSystemProvider>();

  close = output<void>();
  importData = output<{ destPath: string[]; data: FileSystemNode }>();

  selectedPath = signal<string[] | null>(null);
  importedData = signal<FileSystemNode | null>(null);
  fileName = signal<string>('');
  error = signal<string>('');

  @ViewChild('fileInput') fileInput!: ElementRef<HTMLInputElement>;

  onPathChange(path: string[]): void {
    this.selectedPath.set(path);
  }

  onFileSelectClick(): void {
    this.fileInput.nativeElement.click();
  }

  onFileSelected(event: Event): void {
    this.error.set('');
    this.importedData.set(null);
    this.fileName.set('');
    
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];

    if (!file) return;
    
    if (file.type !== 'application/json') {
      this.error.set('Invalid file type. Please select a JSON file.');
      return;
    }

    this.fileName.set(file.name);

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const text = e.target?.result;
        if (typeof text === 'string') {
          const data = JSON.parse(text);
          // Basic validation
          if (data.name && data.type === 'folder') {
            this.importedData.set(data);
          } else {
            throw new Error('JSON file is not a valid folder structure export.');
          }
        }
      } catch (err) {
        this.error.set(`Error parsing file: ${(err as Error).message}`);
      }
    };
    reader.onerror = () => {
      this.error.set('Failed to read the selected file.');
    };
    reader.readAsText(file);
    
    // Reset file input to allow selecting the same file again
    input.value = '';
  }
  
  onImport(): void {
    const path = this.selectedPath();
    const data = this.importedData();
    if (path && data) {
        this.importData.emit({ destPath: path, data });
    }
  }
}