import { Component, Input, Output, EventEmitter, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Requirement } from '../models/requirement.model';
import { RequirementsApiService } from '../services/requirements-api.service';
import { Project } from '../models/project.model';
import { Subsystem } from '../models/subsystem.model';
import { Feature } from '../models/feature.model';

@Component({
  selector: 'app-requirement-form',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="modal-overlay" (click)="cancel.emit()">
      <div class="modal-content" (click)="$event.stopPropagation()">
        <div class="modal-header">
          <h2>{{ requirement ? 'Edit Requirement' : 'New Requirement' }}</h2>
          <button class="close-btn" (click)="cancel.emit()">×</button>
        </div>

        <form (ngSubmit)="handleSubmit()" class="form">
          <div class="form-group">
            <label for="name">Name *</label>
            <input
              id="name"
              type="text"
              [(ngModel)]="formData.name"
              name="name"
              required
              placeholder="Enter requirement name"
              class="form-input">
          </div>

          <div class="form-group">
            <label for="description">Description *</label>
            <textarea
              id="description"
              [(ngModel)]="formData.description"
              name="description"
              required
              rows="4"
              placeholder="Describe the requirement"
              class="form-input"></textarea>
          </div>

          <div class="form-group">
            <label for="status">Status *</label>
            <select
              id="status"
              [(ngModel)]="formData.status"
              name="status"
              required
              class="form-input">
              <option value="pending">Pending</option>
              <option value="in-progress">In Progress</option>
              <option value="complete">Complete</option>
            </select>
          </div>

          <div class="form-group">
            <label for="project">Project</label>
            <select
              id="project"
              [(ngModel)]="formData.projectId"
              name="projectId"
              class="form-input"
              (ngModelChange)="onProjectChange($event)">
              <option value="">Select a Project</option>
              <option *ngFor="let project of projects" [value]="project.id">{{ project.name }}</option>
            </select>
          </div>

          <div class="form-group">
            <label for="subsystem">Subsystem</label>
            <select
              id="subsystem"
              [(ngModel)]="formData.subsystemId"
              name="subsystemId"
              class="form-input"
              [disabled]="!availableSubsystems.length"
              (ngModelChange)="onSubsystemChange($event)">
              <option value="">Select a Subsystem</option>
              <option *ngFor="let subsystem of availableSubsystems" [value]="subsystem.id">{{ subsystem.name }}</option>
            </select>
          </div>

          <div class="form-group">
            <label for="feature">Feature</label>
            <select
              id="feature"
              [(ngModel)]="formData.featureId"
              name="featureId"
              class="form-input"
              [disabled]="!availableFeatures.length">
              <option value="">Select a Feature</option>
              <option *ngFor="let feature of availableFeatures" [value]="feature.id">{{ feature.name }}</option>
            </select>
          </div>

          <div class="form-group">
            <label for="technologies">Technologies</label>
            <div class="tech-input-group">
              <input
                id="technologies"
                type="text"
                [(ngModel)]="techInput"
                (keyup.enter)="addTechnology()"
                name="techInput"
                placeholder="Enter a technology and press Enter"
                class="form-input">
              <button type="button" (click)="addTechnology()" class="add-tech-btn">Add</button>
            </div>
            <div class="tech-tags" *ngIf="formData.technologies.length > 0">
              <span class="tech-tag" *ngFor="let tech of formData.technologies; let i = index">
                {{ tech }}
                <button type="button" (click)="removeTechnology(i)" class="remove-tech">×</button>
              </span>
            </div>
          </div>

          <div class="form-actions">
            <button type="button" (click)="cancel.emit()" class="btn-secondary">
              Cancel
            </button>
            <button type="submit" class="btn-primary" [disabled]="!isValid()">
              {{ requirement ? 'Update' : 'Create' }} Requirement
            </button>
          </div>
        </form>
      </div>
    </div>
  `,
  styles: [`
    .modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0,0,0,0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 1rem;
      animation: fadeIn 0.2s ease;
    }

    .modal-content {
      background: white;
      border-radius: 16px;
      max-width: 600px;
      width: 100%;
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 20px 60px rgba(0,0,0,0.3);
      animation: slideUp 0.3s ease;
    }

    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 1.5rem;
      border-bottom: 1px solid #e2e8f0;
    }

    .modal-header h2 {
      margin: 0;
      font-size: 1.5rem;
      color: #2d3748;
    }

    .close-btn {
      background: transparent;
      border: none;
      font-size: 2rem;
      color: #a0aec0;
      cursor: pointer;
      padding: 0;
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 8px;
      transition: all 0.2s ease;
    }

    .close-btn:hover {
      background: #f7fafc;
      color: #2d3748;
    }

    .form {
      padding: 1.5rem;
    }

    .form-group {
      margin-bottom: 1.5rem;
    }

    .form-group label {
      display: block;
      margin-bottom: 0.5rem;
      font-weight: 600;
      color: #2d3748;
      font-size: 0.95rem;
    }

    .form-input {
      width: 100%;
      padding: 0.75rem;
      border: 2px solid #e2e8f0;
      border-radius: 8px;
      font-size: 1rem;
      transition: all 0.2s ease;
      font-family: inherit;
    }

    .form-input:focus {
      outline: none;
      border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
    }

    textarea.form-input {
      resize: vertical;
      min-height: 100px;
    }

    select.form-input {
      cursor: pointer;
    }

    .tech-input-group {
      display: flex;
      gap: 0.5rem;
    }

    .add-tech-btn {
      padding: 0.75rem 1.5rem;
      background: #667eea;
      color: white;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      white-space: nowrap;
    }

    .add-tech-btn:hover {
      background: #5568d3;
    }

    .tech-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-top: 0.75rem;
    }

    .tech-tag {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 0.5rem 0.75rem;
      border-radius: 8px;
      font-size: 0.875rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      animation: scaleIn 0.2s ease;
    }

    .remove-tech {
      background: rgba(255,255,255,0.2);
      border: none;
      color: white;
      width: 20px;
      height: 20px;
      border-radius: 50%;
      cursor: pointer;
      font-size: 1rem;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s ease;
    }

    .remove-tech:hover {
      background: rgba(255,255,255,0.3);
    }

    .form-actions {
      display: flex;
      gap: 1rem;
      justify-content: flex-end;
      margin-top: 2rem;
      padding-top: 1.5rem;
      border-top: 1px solid #e2e8f0;
    }

    .btn-primary, .btn-secondary {
      padding: 0.875rem 1.75rem;
      border: none;
      border-radius: 10px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }

    .btn-primary {
      background: #667eea;
      color: white;
    }

    .btn-primary:hover:not(:disabled) {
      background: #5568d3;
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
    }

    .btn-primary:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .btn-secondary {
      background: #e2e8f0;
      color: #4a5568;
    }

    .btn-secondary:hover {
      background: #cbd5e0;
    }

    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }

    @keyframes slideUp {
      from {
        opacity: 0;
        transform: translateY(20px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    @keyframes scaleIn {
      from {
        opacity: 0;
        transform: scale(0.8);
      }
      to {
        opacity: 1;
        transform: scale(1);
      }
    }
  `]
})
export class RequirementFormComponent implements OnInit {
  @Input() requirement: Requirement | null = null;
  @Output() save = new EventEmitter<any>();
  @Output() cancel = new EventEmitter<void>();

  projects: Project[] = [];
  availableSubsystems: Subsystem[] = [];
  availableFeatures: Feature[] = [];

  formData = {
    name: '',
    description: '',
    status: 'pending' as 'pending' | 'in-progress' | 'complete',
    technologies: [] as string[],
    projectId: undefined as number | undefined,
    subsystemId: undefined as number | undefined,
    featureId: undefined as number | undefined
  };

  techInput = '';

  constructor(private apiService: RequirementsApiService) {}

  ngOnInit() {
    // Load all projects
    this.apiService.getProjects().subscribe(projects => {
      this.projects = projects;

      if (this.requirement) {
        this.formData = {
          name: this.requirement.name,
          description: this.requirement.description,
          status: this.requirement.status,
          technologies: [...this.requirement.technologies],
          projectId: this.requirement.projectId,
          subsystemId: this.requirement.subsystemId,
          featureId: this.requirement.featureId
        };

        // Load available subsystems based on selected project
        if (this.formData.projectId) {
          this.apiService.getSubsystemsByProject(this.formData.projectId).subscribe(subsystems => {
            this.availableSubsystems = subsystems;

            // Load available features based on selected subsystem
            if (this.formData.subsystemId) {
              this.apiService.getFeaturesBySubsystem(this.formData.subsystemId).subscribe(features => {
                this.availableFeatures = features;
              });
            }
          });
        }
      }
    });
  }

  onProjectChange(projectId: number | null) {
    if (projectId) {
      // Reset subsystem and feature selections when project changes
      this.formData.subsystemId = undefined;
      this.formData.featureId = undefined;

      // Load subsystems for the selected project
      this.apiService.getSubsystemsByProject(projectId).subscribe(subsystems => {
        this.availableSubsystems = subsystems;
      });
    } else {
      // Clear subsystems and features if no project is selected
      this.availableSubsystems = [];
      this.availableFeatures = [];
      this.formData.subsystemId = undefined;
      this.formData.featureId = undefined;
    }
  }

  onSubsystemChange(subsystemId: number | null) {
    if (subsystemId) {
      // Reset feature selection when subsystem changes
      this.formData.featureId = undefined;

      // Load features for the selected subsystem
      this.apiService.getFeaturesBySubsystem(subsystemId).subscribe(features => {
        this.availableFeatures = features;
      });
    } else {
      // Clear features if no subsystem is selected
      this.availableFeatures = [];
      this.formData.featureId = undefined;
    }
  }

  addTechnology() {
    const tech = this.techInput.trim();
    if (tech && !this.formData.technologies.includes(tech)) {
      this.formData.technologies.push(tech);
      this.techInput = '';
    }
  }

  removeTechnology(index: number) {
    this.formData.technologies.splice(index, 1);
  }

  isValid(): boolean {
    return this.formData.name.trim() !== '' && this.formData.description.trim() !== '';
  }

  handleSubmit() {
    if (this.isValid()) {
      // Prepare the form data with only the fields that are needed for the API
      const submitData = {
        name: this.formData.name,
        description: this.formData.description,
        status: this.formData.status,
        technologies: this.formData.technologies,
        projectId: this.formData.projectId,
        subsystemId: this.formData.subsystemId,
        featureId: this.formData.featureId
      };
      this.save.emit(submitData);
    }
  }
}
