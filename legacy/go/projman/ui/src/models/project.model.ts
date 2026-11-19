export interface Project {
  id: number;
  name: string;
  createdAt: string;
  updatedAt: string;
}

export interface CreateProjectDto {
  name: string;
}

export interface UpdateProjectDto {
  name?: string;
}