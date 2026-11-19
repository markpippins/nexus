export type FileType = 'file' | 'folder';

export interface FileItem {
  id: string;
  name: string;
  path: string;
  type: FileType;
  content?: string;
  language?: string;
  parentId?: string | null;
  children?: FileItem[];
  updatedAt?: number;
}

export interface Project {
  id: string;
  name: string;
  description?: string;
  files: FileItem[];
  createdAt: number;
  updatedAt: number;
}

export interface UserPresence {
  id: string;
  name: string;
  color: string;
  activeFileId?: string | null;
  cursorPosition?: { line: number; column: number };
  selection?: { startLine: number; startColumn: number; endLine: number; endColumn: number };
  joinedAt: number;
}

export interface VersionSnapshot {
  id: string;
  projectId: string;
  message: string;
  author: string;
  timestamp: number;
  files: FileItem[];
  hash: string;
}

export interface Judge0Submission {
  source_code: string;
  language_id: number;
  stdin?: string;
  expected_output?: string;
  cpu_time_limit?: number;
  memory_limit?: number;
}

export interface Judge0Result {
  stdout: string | null;
  stderr: string | null;
  compile_output: string | null;
  message: string | null;
  status: {
    id: number;
    description: string;
  };
  time: string | null;
  memory: number | null;
  token?: string;
  isFallback?: boolean;
}

export interface TerminalLog {
  id: string;
  text: string;
  type: 'info' | 'error' | 'success' | 'command' | 'system';
  timestamp: string;
}

export type EditorTheme =
  | 'vs-dark'
  | 'vs-light'
  | 'hc-black'
  | 'monokai'
  | 'one-dark'
  | 'dracula'
  | 'nord'
  | 'github-light';

export type SidebarTab =
  | 'explorer'
  | 'search'
  | 'git'
  | 'judge0'
  | 'collaboration'
  | 'terminal'
  | 'settings';
