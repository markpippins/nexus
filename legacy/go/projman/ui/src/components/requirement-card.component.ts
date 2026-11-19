import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Requirement, SubItem } from '../models/requirement.model';
import { RequirementsApiService } from '../services/requirements-api.service';

@Component({
  selector: 'app-requirement-card',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card" [class.expanded]="isExpanded">
      <div class="card-header">
        <div class="card-title-section">
          <h3 class="card-title">{{ requirement.name }}</h3>
          <span class="status-badge" [class]="'status-' + requirement.status">
            {{ requirement.status }}
          </span>
        </div>
        <div class="card-actions">
          <button class="action-btn" (click)="edit.emit(requirement)" title="Edit">
            ✏️
          </button>
          <button class="action-btn danger" (click)="delete.emit(requirement.id)" title="Delete">
            🗑️
          </button>
        </div>
      </div>

      <p class="card-description">{{ requirement.description }}</p>

      <div class="technologies" *ngIf="requirement.technologies.length > 0">
        <span class="tech-tag" *ngFor="let tech of requirement.technologies">
          {{ tech }}
        </span>
      </div>

      <div class="requirement-context" *ngIf="requirement.project || requirement.subsystem || requirement.feature">
        <div class="context-item" *ngIf="requirement.project">
          <strong>Project:</strong> {{ requirement.project.name }}
        </div>
        <div class="context-item" *ngIf="requirement.subsystem">
          <strong>Subsystem:</strong> {{ requirement.subsystem.name }}
        </div>
        <div class="context-item" *ngIf="requirement.feature">
          <strong>Feature:</strong> {{ requirement.feature.name }}
        </div>
      </div>

      <div class="subitems-section" *ngIf="requirement.subItems && requirement.subItems.length > 0">
        <button class="expand-btn" (click)="isExpanded = !isExpanded">
          <span class="expand-icon" [class.rotated]="isExpanded">▶</span>
          Sub-items ({{ requirement.subItems.length || 0 }})
        </button>

        <div class="subitems-list" *ngIf="isExpanded">
          <div class="subitem" *ngFor="let subItem of requirement.subItems || []">
            <div class="subitem-content">
              <input
                type="checkbox"
                [checked]="subItem.status === 'complete'"
                (change)="toggleSubItemStatus(subItem)"
                class="subitem-checkbox">
              <span class="subitem-name" [class.completed]="subItem.status === 'complete'">
                {{ subItem.name }}
              </span>
              <span class="subitem-status" [class]="'status-' + subItem.status">
                {{ subItem.status }}
              </span>
            </div>
            <button class="delete-subitem-btn" (click)="deleteSubItem(subItem.id)" title="Delete sub-item">
              ×
            </button>
          </div>

          <div class="add-subitem" *ngIf="showAddSubItem">
            <input
              type="text"
              [(ngModel)]="newSubItemName"
              (keyup.enter)="addSubItem()"
              placeholder="Sub-item name"
              class="subitem-input">
            <button class="btn-small btn-primary" (click)="addSubItem()">Add</button>
            <button class="btn-small btn-secondary" (click)="cancelAddSubItem()">Cancel</button>
          </div>

          <button class="add-subitem-btn" *ngIf="!showAddSubItem" (click)="showAddSubItem = true">
            + Add Sub-item
          </button>
        </div>
      </div>

      <div class="add-subitem-section" *ngIf="!requirement.subItems || requirement.subItems.length === 0">
        <button class="add-first-subitem-btn" (click)="showAddSubItem = true; isExpanded = true">
          + Add Sub-item
        </button>
      </div>

      <div class="card-footer">
        <span class="timestamp">Updated: {{ formatDate(requirement.updatedAt) }}</span>
        <span class="progress-indicator">
          {{ getCompletedSubItems() }} / {{ requirement.subItems.length || 0 }} completed
        </span>
      </div>
    </div>
  `,
  styles: [`
    .card {
      background: white;
      border-radius: 16px;
      padding: 1.5rem;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
      transition: all 0.3s ease;
      animation: slideIn 0.3s ease;
    }

    .card:hover {
      transform: translateY(-4px);
      box-shadow: 0 8px 20px rgba(0,0,0,0.15);
    }

    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 1rem;
      gap: 1rem;
    }

    .card-title-section {
      flex: 1;
    }

    .card-title {
      font-size: 1.25rem;
      font-weight: 700;
      color: #2d3748;
      margin: 0 0 0.5rem 0;
    }

    .status-badge {
      display: inline-block;
      padding: 0.375rem 0.875rem;
      border-radius: 20px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .status-pending {
      background: #fef3c7;
      color: #92400e;
    }

    .status-in-progress {
      background: #dbeafe;
      color: #1e40af;
    }

    .status-complete {
      background: #d1fae5;
      color: #065f46;
    }

    .card-actions {
      display: flex;
      gap: 0.5rem;
    }

    .action-btn {
      background: transparent;
      border: none;
      font-size: 1.25rem;
      cursor: pointer;
      padding: 0.5rem;
      border-radius: 8px;
      transition: all 0.2s ease;
    }

    .action-btn:hover {
      background: #f7fafc;
      transform: scale(1.1);
    }

    .action-btn.danger:hover {
      background: #fee;
    }

    .card-description {
      color: #4a5568;
      line-height: 1.6;
      margin-bottom: 1rem;
    }

    .technologies {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-bottom: 1rem;
    }

    .tech-tag {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 0.375rem 0.875rem;
      border-radius: 8px;
      font-size: 0.875rem;
      font-weight: 500;
    }

    .requirement-context {
      margin-bottom: 1rem;
      padding: 0.75rem;
      background: #f0f4f8;
      border-radius: 8px;
      border-left: 4px solid #667eea;
    }

    .context-item {
      margin-bottom: 0.25rem;
      font-size: 0.9rem;
      color: #4a5568;
    }

    .context-item:last-child {
      margin-bottom: 0;
    }

    .subitems-section {
      margin-top: 1rem;
      padding-top: 1rem;
      border-top: 1px solid #e2e8f0;
    }

    .expand-btn {
      background: transparent;
      border: none;
      color: #667eea;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.5rem 0;
      font-size: 0.95rem;
    }

    .expand-icon {
      transition: transform 0.3s ease;
      font-size: 0.75rem;
    }

    .expand-icon.rotated {
      transform: rotate(90deg);
    }

    .subitems-list {
      margin-top: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }

    .subitem {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.75rem;
      background: #f7fafc;
      border-radius: 8px;
      transition: all 0.2s ease;
    }

    .subitem:hover {
      background: #edf2f7;
    }

    .subitem-content {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex: 1;
    }

    .subitem-checkbox {
      width: 18px;
      height: 18px;
      cursor: pointer;
    }

    .subitem-name {
      flex: 1;
      color: #2d3748;
      font-weight: 500;
    }

    .subitem-name.completed {
      text-decoration: line-through;
      color: #a0aec0;
    }

    .subitem-status {
      padding: 0.25rem 0.625rem;
      border-radius: 12px;
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
    }

    .delete-subitem-btn {
      background: transparent;
      border: none;
      color: #e53e3e;
      font-size: 1.5rem;
      cursor: pointer;
      padding: 0.25rem 0.5rem;
      border-radius: 4px;
      transition: all 0.2s ease;
    }

    .delete-subitem-btn:hover {
      background: #fee;
    }

    .add-subitem {
      display: flex;
      gap: 0.5rem;
      margin-top: 0.5rem;
    }

    .subitem-input {
      flex: 1;
      padding: 0.625rem;
      border: 2px solid #e2e8f0;
      border-radius: 8px;
      font-size: 0.95rem;
    }

    .subitem-input:focus {
      outline: none;
      border-color: #667eea;
    }

    .btn-small {
      padding: 0.625rem 1rem;
      border: none;
      border-radius: 8px;
      font-size: 0.875rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }

    .btn-primary {
      background: #667eea;
      color: white;
    }

    .btn-primary:hover {
      background: #5568d3;
    }

    .btn-secondary {
      background: #e2e8f0;
      color: #4a5568;
    }

    .btn-secondary:hover {
      background: #cbd5e0;
    }

    .add-subitem-btn, .add-first-subitem-btn {
      background: transparent;
      border: 2px dashed #cbd5e0;
      color: #667eea;
      padding: 0.625rem;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      width: 100%;
      margin-top: 0.5rem;
      transition: all 0.2s ease;
    }

    .add-subitem-btn:hover, .add-first-subitem-btn:hover {
      border-color: #667eea;
      background: #f7fafc;
    }

    .add-subitem-section {
      margin-top: 1rem;
      padding-top: 1rem;
      border-top: 1px solid #e2e8f0;
    }

    .card-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 1rem;
      padding-top: 1rem;
      border-top: 1px solid #e2e8f0;
      font-size: 0.875rem;
      color: #718096;
    }

    .progress-indicator {
      font-weight: 600;
      color: #667eea;
    }

    @keyframes slideIn {
      from {
        opacity: 0;
        transform: translateY(20px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }
  `]
})
export class RequirementCardComponent {
  @Input() requirement!: Requirement;
  @Output() delete = new EventEmitter<string>();
  @Output() edit = new EventEmitter<Requirement>();
  @Output() refresh = new EventEmitter<void>();

  isExpanded = false;
  showAddSubItem = false;
  newSubItemName = '';

  constructor(private apiService: RequirementsApiService) {}

  formatDate(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  getCompletedSubItems(): number {
    return this.requirement.subItems.filter(item => item.status === 'complete').length || 0;
  }

  toggleSubItemStatus(subItem: SubItem) {
    const newStatus = subItem.status === 'complete' ? 'pending' : 'complete';
    this.apiService.updateSubItem(this.requirement.id, subItem.id, { status: newStatus }).subscribe({
      next: () => this.refresh.emit(),
      error: (err) => console.error('Error updating sub-item:', err)
    });
  }

  addSubItem() {
    if (this.newSubItemName.trim()) {
      this.apiService.addSubItem(this.requirement.id, {
        name: this.newSubItemName,
        status: 'pending'
      }).subscribe({
        next: () => {
          this.newSubItemName = '';
          this.showAddSubItem = false;
          this.refresh.emit();
        },
        error: (err) => console.error('Error adding sub-item:', err)
      });
    }
  }

  cancelAddSubItem() {
    this.showAddSubItem = false;
    this.newSubItemName = '';
  }

  deleteSubItem(subItemId: string) {
    if (confirm('Delete this sub-item?')) {
      this.apiService.deleteSubItem(this.requirement.id, subItemId).subscribe({
        next: () => this.refresh.emit(),
        error: (err) => console.error('Error deleting sub-item:', err)
      });
    }
  }
}
