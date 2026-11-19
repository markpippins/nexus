export type FileType = 'folder' | 'file';

export interface FileSystemNode {
  name: string;
  type: FileType;
  children?: FileSystemNode[];
  content?: string; // for files
  modified?: string; // for display
  childrenLoaded?: boolean;
  // Properties for virtual server profile nodes in the tree
  isServerRoot?: boolean;
  profileId?: string;
  connected?: boolean;
  // Properties for magnet folders
  isMagnet?: boolean;
  magnetFile?: string;
}

export interface SearchResultNode extends FileSystemNode {
  path: string[];
}