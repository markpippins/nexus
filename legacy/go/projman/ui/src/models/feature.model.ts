export interface Feature {
  id: number;
  name: string;
  subsystemId: number;
  createdAt: string;
  updatedAt: string;
  subsystem?: Subsystem;
}

export interface CreateFeatureDto {
  name: string;
  subsystemId: number;
}

export interface UpdateFeatureDto {
  name?: string;
  subsystemId?: number;
}

export interface Subsystem {
  id: number;
  name: string;
  projectId: number;
  createdAt: string;
  updatedAt: string;
  project?: Project;
}

export interface Project {
  id: number;
  name: string;
  createdAt: string;
  updatedAt: string;
}