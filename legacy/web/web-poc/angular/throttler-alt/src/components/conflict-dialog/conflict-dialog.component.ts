import { Component, ChangeDetectionStrategy, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';

export type ConflictResolution = 'merge' | 'replace' | 'skip' | 'cancel';

@Component({
  selector: 'app-conflict-dialog',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './conflict-dialog.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConflictDialogComponent {
  itemName = input.required<string>();
  itemType = input.required<'file' | 'folder'>();
  
  resolve = output<ConflictResolution>();
  close = output<void>();

  onResolve(resolution: ConflictResolution): void {
    this.resolve.emit(resolution);
  }

  onCancel(): void {
    this.close.emit();
  }
}