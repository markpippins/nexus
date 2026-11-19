import { Component, ChangeDetectionStrategy, input, output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FileTreeNode } from '../../services/ui-preferences.service';

/** Recursive tree node component with lazy-loading support */
@Component({
  selector: 'app-tree-node',
  standalone: true,
  imports: [CommonModule, TreeNodeComponent],
  template: `
    @let isExpanded = expandedPaths().has(node().path);
    @let needsChildren = node().type === 'directory' && isExpanded && !node().childrenLoaded;
    <div
      class="flex items-center px-2 py-0.5 cursor-pointer hover:bg-[rgb(var(--color-surface-hover))] transition-colors text-[rgb(var(--color-text-muted))]"
      [class.bg-[rgb(var(--color-accent-bg-selected))]]="selectedPath() === node().path"
      [class.text-[rgb(var(--color-accent-text))]]="selectedPath() === node().path"
      [style.paddingLeft.px]="depth() * 16 + 8"
      (click)="handleClick()"
    >
      <!-- Expand/collapse chevron for directories -->
      @if (node().type === 'directory') {
        <button class="p-0.5 mr-0.5 rounded hover:bg-[rgb(var(--color-surface-hover))]" (click)="$event.stopPropagation(); onToggle()">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 transition-transform" [class.rotate-90]="isExpanded" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
          </svg>
        </button>
      } @else {
        <span class="w-4 mr-0.5"></span>
      }

      <!-- Icon -->
      <span class="mr-1.5 text-sm">
        @if (node().type === 'directory') {
          @if (isExpanded) {
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-[rgb(var(--color-accent-text))]" fill="currentColor" viewBox="0 0 20 20">
              <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
            </svg>
          } @else {
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-[rgb(var(--color-text-subtle))]" fill="currentColor" viewBox="0 0 20 20">
              <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
            </svg>
          }
        } @else {
          <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-[rgb(var(--color-text-subtle))]" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clip-rule="evenodd" />
          </svg>
        }
      </span>

      <span class="truncate text-sm">{{ node().name }}</span>

      <!-- Loading spinner while fetching children -->
      @if (needsChildren) {
        <span class="ml-1 w-3 h-3 border border-[rgb(var(--color-accent-ring))] border-t-transparent rounded-full animate-spin"></span>
      }
    </div>

    <!-- Children (shown when expanded and loaded) -->
    @if (node().type === 'directory' && isExpanded && node().children) {
      @for (child of node().children; track child.path) {
        <app-tree-node
          [node]="child"
          [depth]="depth() + 1"
          [expandedPaths]="expandedPaths()"
          [selectedPath]="selectedPath()"
          (nodeClick)="nodeClick.emit($event)"
          (nodeToggle)="nodeToggle.emit($event)"
          (loadChildren)="loadChildren.emit($event)"
        ></app-tree-node>
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TreeNodeComponent {
  node = input.required<FileTreeNode>();
  depth = input(0);
  expandedPaths = input<Set<string>>(new Set());
  selectedPath = input<string | null>(null);

  nodeClick = output<string>();
  nodeToggle = output<FileTreeNode>();
  loadChildren = output<FileTreeNode>();

  handleClick(): void {
    if (this.node().type === 'directory') {
      this.onToggle();
    } else {
      this.nodeClick.emit(this.node().path);
    }
  }

  onToggle(): void {
    const node = this.node();
    this.nodeToggle.emit(node);
    const currentlyExpanded = this.expandedPaths().has(node.path);
    if (!currentlyExpanded && node.type === 'directory' && !node.childrenLoaded) {
      this.loadChildren.emit(node);
    }
  }
}

@Component({
  selector: 'app-file-tree',
  standalone: true,
  imports: [CommonModule, TreeNodeComponent],
  template: `
    <div class="flex flex-col h-full bg-[rgb(var(--color-surface-sidebar))] text-sm">
      <!-- Header -->
      <div class="flex items-center justify-between px-3 py-2 border-b border-[rgb(var(--color-border-base))] flex-shrink-0">
        <span class="text-sm font-semibold uppercase tracking-wider text-[rgb(var(--color-text-subtle))]">Files</span>
        <div class="flex items-center gap-1">
          <button
            class="p-1 rounded hover:bg-[rgb(var(--color-surface-hover))] text-[rgb(var(--color-text-muted))]"
            title="Refresh"
            (click)="refresh.emit()"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Tree content -->
      <div class="flex-1 overflow-y-auto overflow-x-hidden">
        @if (loading()) {
          <div class="flex items-center justify-center py-8 text-[rgb(var(--color-text-subtle))]">
            <div class="w-5 h-5 border-2 border-[rgb(var(--color-accent-ring))] border-t-transparent rounded-full animate-spin"></div>
          </div>
        } @else if (nodes().length === 0) {
          <div class="px-3 py-6 text-center text-[rgb(var(--color-text-subtle))] text-sm">
            <p>No files to show.</p>
            <p class="mt-1">Connect to a server in Nexus to browse files.</p>
          </div>
        } @else {
          @for (node of nodes(); track node.path) {
            <div>
              <app-tree-node
                [node]="node"
                [depth]="0"
                [expandedPaths]="expandedPaths()"
                [selectedPath]="selectedPath()"
                (nodeClick)="onNodeClick($event)"
                (nodeToggle)="onNodeToggle($event)"
                (loadChildren)="onLoadChildren($event)"
              ></app-tree-node>
            </div>
          }
        }
      </div>
    </div>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FileTreeComponent {
  nodes = input<FileTreeNode[]>([]);
  loading = input(false);
  selectedPath = input<string | null>(null);

  fileSelected = output<string>();
  refresh = output<void>();
  loadChildren = output<FileTreeNode>();

  expandedPaths = signal<Set<string>>(new Set());

  onNodeClick(path: string): void {
    this.fileSelected.emit(path);
  }

  onNodeToggle(node: FileTreeNode): void {
    this.expandedPaths.update(set => {
      const next = new Set(set);
      if (next.has(node.path)) {
        next.delete(node.path);
      } else {
        next.add(node.path);
      }
      return next;
    });
  }

  onLoadChildren(node: FileTreeNode): void {
    this.loadChildren.emit(node);
  }
}
