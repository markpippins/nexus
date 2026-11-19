import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { provideHttpClient } from '@angular/common/http';
import { Requirement, CreateRequirementDto, UpdateRequirementDto, SubItem } from '../models/requirement.model';
import { Project, CreateProjectDto, UpdateProjectDto } from '../models/project.model';
import { Subsystem, CreateSubsystemDto, UpdateSubsystemDto } from '../models/subsystem.model';
import { Feature, CreateFeatureDto, UpdateFeatureDto } from '../models/feature.model';

@Injectable({
  providedIn: 'root'
})
export class RequirementsApiService {
  private apiUrl = 'http://localhost:8073';  // Default port from main.go

  constructor(private http: HttpClient) {}

  getRequirements(): Observable<Requirement[]> {
    return this.http.get<Requirement[]>(`${this.apiUrl}/requirements`);
  }

  getRequirementById(id: string): Observable<Requirement> {
    return this.http.get<Requirement>(`${this.apiUrl}/requirements/${id}`);
  }

  getRequirementsByStatus(status: string): Observable<Requirement[]> {
    return this.http.get<Requirement[]>(`${this.apiUrl}/requirements/status/${status}`);
  }

  createRequirement(requirement: CreateRequirementDto): Observable<Requirement> {
    return this.http.post<Requirement>(`${this.apiUrl}/requirements`, requirement);
  }

  updateRequirement(id: string, requirement: UpdateRequirementDto): Observable<Requirement> {
    return this.http.put<Requirement>(`${this.apiUrl}/requirements/${id}`, requirement);
  }

  deleteRequirement(id: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/requirements/${id}`);
  }

  addSubItem(requirementId: string, subItem: { name: string; status: string }): Observable<SubItem> {
    return this.http.post<SubItem>(`${this.apiUrl}/requirements/${requirementId}/subitems`, subItem);
  }

  updateSubItem(requirementId: string, subItemId: string, subItem: { name?: string; status?: string }): Observable<SubItem> {
    return this.http.put<SubItem>(`${this.apiUrl}/requirements/${requirementId}/subitems/${subItemId}`, subItem);
  }

  deleteSubItem(requirementId: string, subItemId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/requirements/${requirementId}/subitems/${subItemId}`);
  }

  // PROJECT METHODS
  getProjects(): Observable<Project[]> {
    return this.http.get<Project[]>(`${this.apiUrl}/projects`);
  }

  getProjectById(id: number): Observable<Project> {
    return this.http.get<Project>(`${this.apiUrl}/projects/${id}`);
  }

  createProject(project: CreateProjectDto): Observable<Project> {
    return this.http.post<Project>(`${this.apiUrl}/projects`, project);
  }

  updateProject(id: number, project: UpdateProjectDto): Observable<Project> {
    return this.http.put<Project>(`${this.apiUrl}/projects/${id}`, project);
  }

  deleteProject(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/projects/${id}`);
  }

  // SUBSYSTEM METHODS
  getSubsystems(): Observable<Subsystem[]> {
    return this.http.get<Subsystem[]>(`${this.apiUrl}/subsystems`);
  }

  getSubsystemById(id: number): Observable<Subsystem> {
    return this.http.get<Subsystem>(`${this.apiUrl}/subsystems/${id}`);
  }

  createSubsystem(subsystem: CreateSubsystemDto): Observable<Subsystem> {
    return this.http.post<Subsystem>(`${this.apiUrl}/subsystems`, subsystem);
  }

  updateSubsystem(id: number, subsystem: UpdateSubsystemDto): Observable<Subsystem> {
    return this.http.put<Subsystem>(`${this.apiUrl}/subsystems/${id}`, subsystem);
  }

  deleteSubsystem(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/subsystems/${id}`);
  }

  getSubsystemsByProject(projectId: number): Observable<Subsystem[]> {
    return this.http.get<Subsystem[]>(`${this.apiUrl}/projects/${projectId}/subsystems`);
  }

  // FEATURE METHODS
  getFeatures(): Observable<Feature[]> {
    return this.http.get<Feature[]>(`${this.apiUrl}/features`);
  }

  getFeatureById(id: number): Observable<Feature> {
    return this.http.get<Feature>(`${this.apiUrl}/features/${id}`);
  }

  createFeature(feature: CreateFeatureDto): Observable<Feature> {
    return this.http.post<Feature>(`${this.apiUrl}/features`, feature);
  }

  updateFeature(id: number, feature: UpdateFeatureDto): Observable<Feature> {
    return this.http.put<Feature>(`${this.apiUrl}/features/${id}`, feature);
  }

  deleteFeature(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/features/${id}`);
  }

  getFeaturesBySubsystem(subsystemId: number): Observable<Feature[]> {
    return this.http.get<Feature[]>(`${this.apiUrl}/subsystems/${subsystemId}/features`);
  }
}
