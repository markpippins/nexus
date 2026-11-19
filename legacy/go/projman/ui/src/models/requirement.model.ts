import { Project } from './project.model';
import { Subsystem } from './subsystem.model';
import { Feature } from './feature.model';

export interface SubItem {
  id: string;
  name: string;
  status: 'pending' | 'in-progress' | 'complete';
}

export interface Requirement {
  id: string;
  name: string;
  description: string;
  status: 'pending' | 'in-progress' | 'complete';
  technologies: string[];
  projectId?: number;
  subsystemId?: number;
  featureId?: number;
  project?: Project;
  subsystem?: Subsystem;
  feature?: Feature;
  subItems: SubItem[];
  createdAt: string;
  updatedAt: string;
}

export interface CreateRequirementDto {
  name: string;
  description: string;
  status: 'pending' | 'in-progress' | 'complete';
  technologies: string[];
  projectId?: number;
  subsystemId?: number;
  featureId?: number;
}

export interface UpdateRequirementDto {
  name?: string;
  description?: string;
  status?: 'pending' | 'in-progress' | 'complete';
  technologies?: string[];
  projectId?: number;
  subsystemId?: number;
  featureId?: number;
}
