import { Component, ChangeDetectionStrategy, input, output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FileSystemNode } from '../../models/file-system.model.js';
import { ImageService } from '../../services/image.service.js';
import { FileSystemProvider } from '../../services/file-system-provider.js';
import { TreeViewComponent } from '../tree-view/tree-view.component.js';

@Component({
  selector: 'app-export-dialog',
  standalone: true,
  imports: [CommonModule, TreeViewComponent],
  templateUrl: './export-dialog.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ExportDialogComponent {
  localSessionNode = input.required<FileSystemNode | null>();
  getImageService = input.required<(path: string[]) => ImageService>();
  getProvider = input.required<(path: string[]) => FileSystemProvider>();

  close = output<void>();
  exportData = output<{ node: FileSystemNode; path: string[] }>();

  selectedPath = signal<string[] | null>(null);

  onPathChange(path: string[]): void {
    this.selectedPath.set(path);
  }

  private findNodeByPath(root: FileSystemNode, path: string[]): FileSystemNode | null {
    if (path.length === 0) return root;
    let currentNode: FileSystemNode | undefined = root;
    for (const segment of path) {
      currentNode = currentNode?.children?.find(c => c.name === segment);
      if (!currentNode) return null;
    }
    return currentNode ?? null;
  }
  
  private sanitizeForExport(node: FileSystemNode): FileSystemNode {
    const sanitizedNode: FileSystemNode = {
      name: node.name,
      type: 'folder',
    };

    if (node.children && node.children.length > 0) {
      const folderChildren = node.children.filter(child => child.type === 'folder');
      if (folderChildren.length > 0) {
        sanitizedNode.children = folderChildren.map(child => this.sanitizeForExport(child));
      }
    }
    return sanitizedNode;
  }

  onExport(): void {
    const path = this.selectedPath();
    const root = this.localSessionNode();
    if (!path || !root) return;
    
    const nodeToExport = this.findNodeByPath(root, path);
    if (nodeToExport) {
        const sanitizedNode = this.sanitizeForExport(nodeToExport);
        this.exportData.emit({ node: sanitizedNode, path });
    }
  }
}