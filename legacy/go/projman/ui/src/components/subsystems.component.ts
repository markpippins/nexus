import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subsystem, UpdateSubsystemDto } from '../models/subsystem.model';
import { RequirementsApiService } from '../services/requirements-api.service';

@Component({
  selector: 'app-subsystems',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="subsystems-container">
      <header class="subsystems-header">
        <h2>Subsystems</h2>
        <button class="btn-primary" (click)="showCreateForm = true">
          <span class="btn-icon">+</span> New Subsystem
        </button>
      </header>

      <div class="subsystems-table" *ngIf="!loading && subsystems.length > 0">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Project</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr *ngFor="let subsystem of subsystems">
              <td>{{ subsystem.id }}</td>
              <td>{{ subsystem.name }}</td>
              <td>{{ subsystem.project?.name || 'N/A' }}</td>
              <td>{{ formatDate(subsystem.createdAt) }}</td>
              <td>
                <button class="btn-sm btn-edit" (click)="editSubsystem(subsystem)">✏️</button>
                <button class="btn-sm btn-delete" (click)="deleteSubsystem(subsystem.id)">🗑️</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="empty-state" *ngIf="!loading && subsystems.length === 0">
        <div class="empty-icon">⚙️</div>
        <h3>No subsystems found</h3>
        <p>Create projects first, then add subsystems to them</p>
        <button class="btn-primary" (click)="showCreateForm = true">Create Subsystem</button>
      </div>

      <div class="loading-state" *ngIf="loading">
        <div class="spinner"></div>
        <p>Loading subsystems...</p>
      </div>

      <!-- Subsystem Form Modal -->
      <div class="modal-overlay" *ngIf="showCreateForm || editingSubsystem">
        <div class="modal">
          <div class="modal-header">
            <h3>{{ editingSubsystem ? 'Edit Subsystem' : 'Create Subsystem' }}</h3>
            <button class="btn-close" (click)="cancelForm()">×</button>
          </div>
          <div class="modal-body">
            <div class="form-group">
              <label for="subsystemName">Subsystem Name</label>
              <input
                id="subsystemName"
                type="text"
                [(ngModel)]="formData.name"
                placeholder="Enter subsystem name"
                class="form-control"
              />
            </div>
            <div class="form-group" *ngIf="!editingSubsystem">
              <label for="projectSelect">Project</label>
              <select
                id="projectSelect"
                [(ngModel)]="formData.projectId"
                class="form-control"
              >
                <option value="">Select a project</option>
                <option *ngFor="let project of projects" [value]="project.id">
                  {{ project.name }}
                </option>
              </select>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn-secondary" (click)="cancelForm()">Cancel</button>
            <button class="btn-primary" (click)="saveSubsystem()" [disabled]="!formData.name.trim()">
              {{ editingSubsystem ? 'Update' : 'Create' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .subsystems-container {
      padding: 2rem;
      max-width: 1200px;
      margin: 0 auto;
    }

    .subsystems-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2rem;
    }

    .subsystems-table {
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
export class SubsystemsComponent implements OnInit {
  subsystems: Subsystem[] = [];
  projects: any[] = [];
  loading = true;
  showCreateForm = false;
  editingSubsystem: Subsystem | null = null;
  formData = { name: '', projectId: 0 };

  constructor(private apiService: RequirementsApiService) {}

  ngOnInit() {
    this.loadSubsystems();
    this.loadProjects();
  }

  loadSubsystems() {
    this.loading = true;
    this.apiService.getSubsystems().subscribe({
      next: (data) => {
        this.subsystems = data;
        this.loading = false;
      },
      error: (err) => {
        console.error('Error loading subsystems:', err);
        this.subsystems = [];
        this.loading = false;
      }
    });
  }

  loadProjects() {
    this.apiService.getProjects().subscribe({
      next: (data) => {
        this.projects = data;
      },
      error: (err) => {
        console.error('Error loading projects:', err);
        this.projects = [];
      }
    });
  }

  editSubsystem(subsystem: Subsystem) {
    this.editingSubsystem = subsystem;
    this.formData = { name: subsystem.name, projectId: subsystem.projectId || 0 };
    this.showCreateForm = true;
  }

  saveSubsystem() {
    if (this.editingSubsystem) {
      // For updates, only include projectId if it's a valid positive number
      const updateData: Partial<UpdateSubsystemDto> = { name: this.formData.name };
      if (this.formData.projectId > 0) {
        updateData.projectId = this.formData.projectId;
      }
      this.apiService.updateSubsystem(this.editingSubsystem.id, updateData).subscribe({
        next: () => {
          this.loadSubsystems();
          this.cancelForm();
        },
        error: (err) => console.error('Error updating subsystem:', err)
      });
    } else {
      // For creates, projectId must be provided and valid
      if (!this.formData.projectId || this.formData.projectId <= 0) {
        console.error('Valid Project ID is required for creating a subsystem');
        return;
      }
      this.apiService.createSubsystem(this.formData).subscribe({
        next: () => {
          this.loadSubsystems();
          this.cancelForm();
        },
        error: (err) => console.error('Error creating subsystem:', err)
      });
    }
  }

  deleteSubsystem(id: number) {
    if (confirm('Are you sure you want to delete this subsystem?')) {
      this.apiService.deleteSubsystem(id).subscribe({
        next: () => this.loadSubsystems(),
        error: (err) => console.error('Error deleting subsystem:', err)
      });
    }
  }

  cancelForm() {
    this.showCreateForm = false;
    this.editingSubsystem = null;
    this.formData = { name: '', projectId: 0 };
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