export type Status = 'pending' | 'in-progress' | 'complete';

export interface SubItem {
  id: string;
  name: string;
  status: Status;
}

export interface Requirement {
  id: string;
  name: string;
  description: string;
  status: Status;
  technologies: string[];
  subItems: SubItem[];
  createdAt: string;
  updatedAt: string;
}
