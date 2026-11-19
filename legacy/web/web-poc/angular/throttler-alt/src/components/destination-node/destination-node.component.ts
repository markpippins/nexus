import { Component, ChangeDetectionStrategy, input, output, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FileSystemNode } from '../../models/file-system.model.js';

@Component({
  selector: 'app-destination-node',
  imports: [CommonModule, DestinationNodeComponent], // Recursive import
  templateUrl: './destination-node.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DestinationNodeComponent {
  node = input.required<FileSystemNode>();
  path = input.required<string[]>();
  currentSourcePath = input<string[]>([]);
  destinationSelected = output<string[]>();

  isSubmenuOpen = signal(false);

  // Computed property to check if this node should be disabled
  isDisabled = computed(() => {
    // Compare array paths by joining them into a string.
    // This is simple and effective for this use case.
    return this.path().join('/') === this.currentSourcePath().join('/');
  });

  // Computed properties to simplify the template
  folderChildren = computed(() => {
    const children = this.node().children;
    return children ? children.filter(c => c.type === 'folder') : [];
  });

  hasFolderChildren = computed(() => {
    return this.folderChildren().length > 0;
  });

  selectDestination(): void {
    if (this.isDisabled()) {
      return; // Do not emit if the node is disabled
    }
    if (this.node().type === 'folder') {
      this.destinationSelected.emit(this.path());
    }
  }

  onChildSelected(path: string[]): void {
    this.destinationSelected.emit(path);
  }

  getChildPath(childNode: FileSystemNode): string[] {
    return [...this.path(), childNode.name];
  }
}
