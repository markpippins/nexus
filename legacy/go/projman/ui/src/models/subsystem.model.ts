export interface Subsystem {
  id: number;
  name: string;
  projectId: number;
  createdAt: string;
  updatedAt: string;
  project?: Project;
}

export interface CreateSubsystemDto {
  name: string;
  projectId: number;
}

export interface UpdateSubsystemDto {
  name?: string;
  projectId?: number;
}

export interface Project {
  id: number;
  name: string;
  createdAt: string;
  updatedAt: string;
}