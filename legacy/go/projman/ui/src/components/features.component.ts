import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Feature, UpdateFeatureDto } from '../models/feature.model';
import { RequirementsApiService } from '../services/requirements-api.service';

@Component({
  selector: 'app-features',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="features-container">
      <header class="features-header">
        <h2>Features</h2>
        <button class="btn-primary" (click)="showCreateForm = true">
          <span class="btn-icon">+</span> New Feature
        </button>
      </header>

      <div class="features-table" *ngIf="!loading && features.length > 0">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Subsystem</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr *ngFor="let feature of features">
              <td>{{ feature.id }}</td>
              <td>{{ feature.name }}</td>
              <td>{{ feature.subsystem?.name || 'N/A' }}</td>
              <td>{{ formatDate(feature.createdAt) }}</td>
              <td>
                <button class="btn-sm btn-edit" (click)="editFeature(feature)">✏️</button>
                <button class="btn-sm btn-delete" (click)="deleteFeature(feature.id)">🗑️</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="empty-state" *ngIf="!loading && features.length === 0">
        <div class="empty-icon">⚡</div>
        <h3>No features found</h3>
        <p>Create projects and subsystems first, then add features</p>
        <button class="btn-primary" (click)="showCreateForm = true">Create Feature</button>
      </div>

      <div class="loading-state" *ngIf="loading">
        <div class="spinner"></div>
        <p>Loading features...</p>
      </div>

      <!-- Feature Form Modal -->
      <div class="modal-overlay" *ngIf="showCreateForm || editingFeature">
        <div class="modal">
          <div class="modal-header">
            <h3>{{ editingFeature ? 'Edit Feature' : 'Create Feature' }}</h3>
            <button class="btn-close" (click)="cancelForm()">×</button>
          </div>
          <div class="modal-body">
            <div class="form-group">
              <label for="featureName">Feature Name</label>
              <input
                id="featureName"
                type="text"
                [(ngModel)]="formData.name"
                placeholder="Enter feature name"
                class="form-control"
              />
            </div>
            <div class="form-group" *ngIf="!editingFeature">
              <label for="subsystemSelect">Subsystem</label>
              <select
                id="subsystemSelect"
                [(ngModel)]="formData.subsystemId"
                class="form-control"
              >
                <option value="">Select a subsystem</option>
                <option *ngFor="let subsystem of subsystems" [value]="subsystem.id">
                  {{ subsystem.name }}
                </option>
              </select>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn-secondary" (click)="cancelForm()">Cancel</button>
            <button class="btn-primary" (click)="saveFeature()" [disabled]="!formData.name.trim()">
              {{ editingFeature ? 'Update' : 'Create' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .features-container {
      padding: 2rem;
      max-width: 1200px;
      margin: 0 auto;
    }

    .features-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2rem;
    }

    .features-table {
      background: white;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }

    table {
      width: 100%;
      border-collapse: collapse;
    }

    th, td {
      padding: 1rem;
      text-align: left;
      border-bottom: 1px solid #e2e8f0;
    }

    th {
      background: #f8f9fa;
      font-weight: 600;
      color: #2d3748;
    }

    tbody tr:hover {
      background: #f8f9fa;
    }

    .btn-sm {
      padding: 0.5rem;
      font-size: 0.875rem;
      margin-right: 0.5rem;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.2s ease;
    }

    .btn-edit {
      background: #e3f2fd;
      color: white;
    }

    .btn-edit:hover {
      background: #c41da3;
    }

    .btn-delete {
      background: #ef4444;
      color: white;
    }

    .btn-delete:hover {
      background: #dc2626;
    }

    .modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0,0,0,0.5);
      display: flex;
      justify-content: center;
      align-items: center;
      z-index: 1000;
    }

    .modal {
      background: white;
      border-radius: 12px;
      padding: 2rem;
      max-width: 500px;
      width: 90%;
      max-height: 80vh;
      overflow-y: auto;
      box-shadow: 0 20px 25px rgba(0,0,0,0.15);
    }

    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.5rem;
    }

    .modal-header h3 {
      margin: 0;
      color: #2d3748;
    }

    .btn-close {
      background: none;
      border: none;
      font-size: 1.5rem;
      cursor: pointer;
      color: #6b7280;
      padding: 0.5rem;
      border-radius: 50%;
      transition: all 0.2s ease;
    }

    .btn-close:hover {
      background: #f3f4f6;
      color: white;
    }

    .modal-footer {
      display: flex;
      justify-content: flex-end;
      gap: 1rem;
      margin-top: 2rem;
    }

    .form-group {
      margin-bottom: 1.5rem;
    }

    .form-group label {
      display: block;
      margin-bottom: 0.5rem;
      font-weight: 600;
      color: #374151;
    }

    .form-control {
      width: 100%;
      padding: 0.75rem;
      border: 2px solid #e5e7eb;
      border-radius: 8px;
      font-size: 1rem;
      transition: border-color 0.2s ease;
    }

    .form-control:focus {
      outline: none;
      border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
    }

    .empty-state, .loading-state {
      text-align: center;
      padding: 4rem 2rem;
      background: rgba(255,255,255,0.95);
      border-radius: 16px;
      box-shadow: 0 10px 40px rgba(0,0,0,0.1);
    }

    .empty-icon {
      font-size: 4rem;
      margin-bottom: 1rem;
      opacity: 0.5;
    }

    .spinner {
      width: 40px;
      height: 40px;
      border: 4px solid #e2e8f0;
      border-top-color: #667eea;
      border-radius: 50%;
      animation: spin 1s linear infinite;
      margin: 0 auto 1rem;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }
  `]
})
export class FeaturesComponent implements OnInit {
  features: Feature[] = [];
  subsystems: any[] = [];
  loading = true;
  showCreateForm = false;
  editingFeature: Feature | null = null;
  formData = { name: '', subsystemId: 0 };

  constructor(private apiService: RequirementsApiService) {}

  ngOnInit() {
    this.loadFeatures();
    this.loadSubsystems();
  }

  loadFeatures() {
    this.loading = true;
    this.apiService.getFeatures().subscribe({
      next: (data) => {
        this.features = data;
        this.loading = false;
      },
      error: (err) => {
        console.error('Error loading features:', err);
        this.features = [];
        this.loading = false;
      }
    });
  }

  loadSubsystems() {
    this.apiService.getSubsystems().subscribe({
      next: (data) => {
        this.subsystems = data;
      },
      error: (err) => {
        console.error('Error loading subsystems:', err);
        this.subsystems = [];
      }
    });
  }

  editFeature(feature: Feature) {
    this.editingFeature = feature;
    this.formData = { name: feature.name, subsystemId: feature.subsystemId || 0 };
    this.showCreateForm = true;
  }

  saveFeature() {
    if (this.editingFeature) {
      // For updates, only include subsystemId if it's a valid positive number
      const updateData: Partial<UpdateFeatureDto> = { name: this.formData.name };
      if (this.formData.subsystemId > 0) {
        updateData.subsystemId = this.formData.subsystemId;
      }
      this.apiService.updateFeature(this.editingFeature.id, updateData).subscribe({
        next: () => {
          this.loadFeatures();
          this.cancelForm();
        },
        error: (err) => console.error('Error updating feature:', err)
      });
    } else {
      // For creates, subsystemId must be provided and valid
      if (!this.formData.subsystemId || this.formData.subsystemId <= 0) {
        console.error('Valid Subsystem ID is required for creating a feature');
        return;
      }
      this.apiService.createFeature(this.formData).subscribe({
        next: () => {
          this.loadFeatures();
          this.cancelForm();
        },
        error: (err) => console.error('Error creating feature:', err)
      });
    }
  }

  deleteFeature(id: number) {
    if (confirm('Are you sure you want to delete this feature?')) {
      this.apiService.deleteFeature(id).subscribe({
        next: () => this.loadFeatures(),
        error: (err) => console.error('Error deleting feature:', err)
      });
    }
  }

  cancelForm() {
    this.showCreateForm = false;
    this.editingFeature = null;
    this.formData = { name: '', subsystemId: 0 };
  }

  formatDate(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { 
      month: 'short', 
      day: 'numeric', 
      year: 'numeric' 
    });
  }
}