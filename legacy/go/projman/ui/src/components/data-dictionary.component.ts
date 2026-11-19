import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RequirementsApiService } from '../services/requirements-api.service';
import { Project, CreateProjectDto } from '../models/project.model';
import { Subsystem, CreateSubsystemDto } from '../models/subsystem.model';
import { Feature, CreateFeatureDto } from '../models/feature.model';
import { timeout, catchError } from 'rxjs/operators';
import { of } from 'rxjs';

@Component({
  selector: 'app-data-dictionary',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="data-dictionary">
      <h2>Data Dictionary</h2>
      
      <!-- Projects Section -->
      <section class="dictionary-section">
        <div class="section-header">
          <h3>Projects</h3>
          <button class="btn-primary" (click)="showProjectForm = true">Add Project</button>
        </div>
        
        <div class="form-container" *ngIf="showProjectForm">
          <input 
            type="text" 
            [(ngModel)]="newProjectName" 
            placeholder="Project name" 
            class="form-input">
          <button class="btn-primary" (click)="createProject()">Create</button>
          <button class="btn-secondary" (click)="cancelProjectForm()">Cancel</button>
        </div>
        
        <div class="items-grid">
          <div class="item-card" *ngFor="let project of projects">
            <div class="item-header">
              <h4>{{ project.name }}</h4>
              <div class="actions">
                <button class="action-btn edit" (click)="editProject(project)">Edit</button>
                <button class="action-btn delete" (click)="deleteProject(project.id)">Delete</button>
              </div>
            </div>
            <p class="item-meta">ID: {{ project.id }} | Created: {{ formatDate(project.createdAt) }}</p>
          </div>
        </div>
      </section>
      
      <!-- Subsystems Section -->
      <section class="dictionary-section">
        <div class="section-header">
          <h3>Subsystems</h3>
          <button class="btn-primary" (click)="showSubsystemForm = true">Add Subsystem</button>
        </div>
        
        <div class="form-container" *ngIf="showSubsystemForm">
          <input 
            type="text" 
            [(ngModel)]="newSubsystemName" 
            placeholder="Subsystem name" 
            class="form-input">
          <select [(ngModel)]="newSubsystemProjectId" class="form-input">
            <option value="">Select Project</option>
            <option *ngFor="let project of projects" [value]="project.id">{{ project.name }}</option>
          </select>
          <button class="btn-primary" (click)="createSubsystem()">Create</button>
          <button class="btn-secondary" (click)="cancelSubsystemForm()">Cancel</button>
        </div>
        
        <div class="items-grid">
          <div class="item-card" *ngFor="let subsystem of subsystems">
            <div class="item-header">
              <h4>{{ subsystem.name }}</h4>
              <div class="actions">
                <button class="action-btn edit" (click)="editSubsystem(subsystem)">Edit</button>
                <button class="action-btn delete" (click)="deleteSubsystem(subsystem.id)">Delete</button>
              </div>
            </div>
            <p class="item-meta">ID: {{ subsystem.id }} | Project: {{ subsystem.project?.name || subsystem.projectId }} | Created: {{ formatDate(subsystem.createdAt) }}</p>
          </div>
        </div>
      </section>
      
      <!-- Features Section -->
      <section class="dictionary-section">
        <div class="section-header">
          <h3>Features</h3>
          <button class="btn-primary" (click)="showFeatureForm = true">Add Feature</button>
        </div>
        
        <div class="form-container" *ngIf="showFeatureForm">
          <input 
            type="text" 
            [(ngModel)]="newFeatureName" 
            placeholder="Feature name" 
            class="form-input">
          <select [(ngModel)]="newFeatureSubsystemId" class="form-input">
            <option value="">Select Subsystem</option>
            <option *ngFor="let subsystem of subsystems" [value]="subsystem.id">{{ subsystem.name }} ({{ subsystem.project?.name }})</option>
          </select>
          <button class="btn-primary" (click)="createFeature()">Create</button>
          <button class="btn-secondary" (click)="cancelFeatureForm()">Cancel</button>
        </div>
        
        <div class="items-grid">
          <div class="item-card" *ngFor="let feature of features">
            <div class="item-header">
              <h4>{{ feature.name }}</h4>
              <div class="actions">
                <button class="action-btn edit" (click)="editFeature(feature)">Edit</button>
                <button class="action-btn delete" (click)="deleteFeature(feature.id)">Delete</button>
              </div>
            </div>
            <p class="item-meta">ID: {{ feature.id }} | Subsystem: {{ feature.subsystem?.name || feature.subsystemId }} | Created: {{ formatDate(feature.createdAt) }}</p>
          </div>
        </div>
      </section>
    </div>
  `,
  styles: [`
    .data-dictionary {
      padding: 2rem;
      background: white;
      border-radius: 16px;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
      margin-bottom: 2rem;
    }
    
    .dictionary-section {
      margin-bottom: 3rem;
    }
    
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }
    
    .section-header h3 {
      margin: 0;
      color: #2d3748;
      font-size: 1.5rem;
    }
    
    .form-container {
      display: flex;
      gap: 1rem;
      margin-bottom: 1.5rem;
      flex-wrap: wrap;
    }
    
    .form-input {
      flex: 1;
      min-width: 200px;
      padding: 0.75rem;
      border: 2px solid #e2e8f0;
      border-radius: 8px;
      font-size: 1rem;
    }
    
    .form-input:focus {
      outline: none;
      border-color: #667eea;
    }
    
    .items-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 1rem;
    }
    
    .item-card {
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 1rem;
      background: #f7fafc;
    }
    
    .item-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 0.5rem;
    }
    
    .item-header h4 {
      margin: 0;
      color: #2d3748;
      font-size: 1.1rem;
    }
    
    .actions {
      display: flex;
      gap: 0.5rem;
    }
    
    .action-btn {
      padding: 0.25rem 0.5rem;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-size: 0.8rem;
    }
    
    .edit {
      background: #667eea;
      color: white;
    }
    
    .delete {
      background: #e53e3e;
      color: white;
    }
    
    .item-meta {
      margin: 0;
      font-size: 0.85rem;
      color: #718096;
    }
  `]
})
export class DataDictionaryComponent implements OnInit {
  projects: Project[] = [];
  subsystems: Subsystem[] = [];
  features: Feature[] = [];
  
  showProjectForm = false;
  showSubsystemForm = false;
  showFeatureForm = false;
  
  newProjectName = '';
  newSubsystemName = '';
  newFeatureName = '';
  newSubsystemProjectId: number | null = null;
  newFeatureSubsystemId: number | null = null;

  constructor(private apiService: RequirementsApiService) {}

  ngOnInit() {
    // Don't load data on initialization, only when the component becomes visible
    // The parent component will call loadData when needed
  }

  loadData() {
    // Load projects with timeout and error handling
    this.apiService.getProjects()
      .pipe(
        timeout(10000), // 10 second timeout
        catchError(error => {
          console.error('Error loading projects:', error);
          return of([]); // Return empty array on error
        })
      )
      .subscribe(projects => {
        this.projects = projects;
        this.loadSubsystems();
      });
  }

  loadSubsystems() {
    this.apiService.getSubsystems()
      .pipe(
        timeout(10000), // 10 second timeout
        catchError(error => {
          console.error('Error loading subsystems:', error);
          return of([]); // Return empty array on error
        })
      )
      .subscribe(subsystems => {
        this.subsystems = subsystems;
        this.loadFeatures();
      });
  }

  loadFeatures() {
    this.apiService.getFeatures()
      .pipe(
        timeout(10000), // 10 second timeout
        catchError(error => {
          console.error('Error loading features:', error);
          return of([]); // Return empty array on error
        })
      )
      .subscribe(features => {
        this.features = features;
      });
  }

  createProject() {
    if (this.newProjectName.trim()) {
      const projectData: CreateProjectDto = {
        name: this.newProjectName.trim()
      };
      
      this.apiService.createProject(projectData).subscribe(createdProject => {
        this.projects.push(createdProject);
        this.cancelProjectForm();
      });
    }
  }

  cancelProjectForm() {
    this.showProjectForm = false;
    this.newProjectName = '';
  }

  deleteProject(id: number) {
    if (confirm('Are you sure you want to delete this project?')) {
      this.apiService.deleteProject(id).subscribe(() => {
        this.projects = this.projects.filter(p => p.id !== id);
        // Also remove any subsystems associated with this project
        this.subsystems = this.subsystems.filter(s => s.projectId !== id);
      });
    }
  }

  createSubsystem() {
    if (this.newSubsystemName.trim() && this.newSubsystemProjectId) {
      const subsystemData: CreateSubsystemDto = {
        name: this.newSubsystemName.trim(),
        projectId: this.newSubsystemProjectId
      };
      
      this.apiService.createSubsystem(subsystemData).subscribe(createdSubsystem => {
        this.subsystems.push(createdSubsystem);
        this.cancelSubsystemForm();
      });
    }
  }

  cancelSubsystemForm() {
    this.showSubsystemForm = false;
    this.newSubsystemName = '';
    this.newSubsystemProjectId = null;
  }

  deleteSubsystem(id: number) {
    if (confirm('Are you sure you want to delete this subsystem?')) {
      this.apiService.deleteSubsystem(id).subscribe(() => {
        this.subsystems = this.subsystems.filter(s => s.id !== id);
        // Also remove any features associated with this subsystem
        this.features = this.features.filter(f => f.subsystemId !== id);
      });
    }
  }

  createFeature() {
    if (this.newFeatureName.trim() && this.newFeatureSubsystemId) {
      const featureData: CreateFeatureDto = {
        name: this.newFeatureName.trim(),
        subsystemId: this.newFeatureSubsystemId
      };
      
      this.apiService.createFeature(featureData).subscribe(createdFeature => {
        this.features.push(createdFeature);
        this.cancelFeatureForm();
      });
    }
  }

  cancelFeatureForm() {
    this.showFeatureForm = false;
    this.newFeatureName = '';
    this.newFeatureSubsystemId = null;
  }

  deleteFeature(id: number) {
    if (confirm('Are you sure you want to delete this feature?')) {
      this.apiService.deleteFeature(id).subscribe(() => {
        this.features = this.features.filter(f => f.id !== id);
      });
    }
  }

  formatDate(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  editProject(project: Project) {
    const newName = prompt('Edit project name:', project.name);
    if (newName !== null && newName.trim() !== '') {
      this.apiService.updateProject(project.id, { name: newName.trim() }).subscribe(updatedProject => {
        const index = this.projects.findIndex(p => p.id === project.id);
        if (index !== -1) {
          this.projects[index] = updatedProject;
        }
      });
    }
  }

  editSubsystem(subsystem: Subsystem) {
    const newName = prompt('Edit subsystem name:', subsystem.name);
    if (newName !== null && newName.trim() !== '') {
      this.apiService.updateSubsystem(subsystem.id, { name: newName.trim(), projectId: subsystem.projectId }).subscribe(updatedSubsystem => {
        const index = this.subsystems.findIndex(s => s.id === subsystem.id);
        if (index !== -1) {
          this.subsystems[index] = updatedSubsystem;
        }
      });
    }
  }

  editFeature(feature: Feature) {
    const newName = prompt('Edit feature name:', feature.name);
    if (newName !== null && newName.trim() !== '') {
      this.apiService.updateFeature(feature.id, { name: newName.trim(), subsystemId: feature.subsystemId }).subscribe(updatedFeature => {
        const index = this.features.findIndex(f => f.id === feature.id);
        if (index !== -1) {
          this.features[index] = updatedFeature;
        }
      });
    }
  }
}