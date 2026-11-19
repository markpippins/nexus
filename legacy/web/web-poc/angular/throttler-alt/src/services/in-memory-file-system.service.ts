import { Injectable, signal, effect, inject } from '@angular/core';
import { FileSystemNode, SearchResultNode } from '../models/file-system.model.js';
import { FileSystemProvider, ItemReference } from './file-system-provider.js';
import { LocalConfigService } from './local-config.service.js';
import { DbService } from './db.service.js';

const SESSION_FS_STORAGE_KEY = 'file-explorer-session-fs';

/**
 * A safe, recursive function to deep-clone a FileSystemNode. This avoids potential
 * issues with JSON.stringify (like converting Date objects to strings) and only
 * copies properties defined in the model.
 */
function cloneNode(node: FileSystemNode): FileSystemNode {
  const newNode: FileSystemNode = {
    name: node.name,
    type: node.type,
    modified: node.modified,
    // FIX: For an in-memory provider, if the children array exists, its contents are loaded.
    // This prevents the tree view from trying to lazy-load nodes that are already present.
    childrenLoaded: !!node.children,
    isMagnet: node.isMagnet,
    magnetFile: node.magnetFile,
  };
  if (node.content !== undefined) {
    newNode.content = node.content;
  }
  if (node.children) {
    newNode.children = node.children.map(cloneNode);
  }
  return newNode;
}

@Injectable({
  providedIn: 'root',
})
export class SessionService implements FileSystemProvider {
  private localConfigService = inject(LocalConfigService);
  private dbService = inject(DbService);
  private rootNode = signal<FileSystemNode>({
    name: '', // Name will be set from config service
    type: 'folder',
    children: [
      {
        name: 'Documents',
        type: 'folder',
        children: [
          { name: 'README.md', type: 'file', content: '# Documents Folder\n\nThis folder contains project briefs and quarterly results.', modified: '2023-10-26T09:00:00Z' },
          { name: 'project-brief.docx', type: 'file', content: 'Project brief content.', modified: '2023-10-26T10:00:00Z' },
          { name: 'quarterly-results.xlsx', type: 'file', content: 'Spreadsheet data.', modified: '2023-10-25T14:30:00Z' },
        ],
        modified: '2023-10-26T10:00:00Z'
      },
      {
        name: 'Pictures',
        type: 'folder',
        children: [
          { name: 'logo.png', type: 'file', content: 'image data', modified: '2023-09-15T11:00:00Z' },
          { name: 'team-photo.jpg', type: 'file', content: 'image data', modified: '2023-09-10T18:00:00Z' },
        ],
        modified: '2023-09-15T11:00:00Z'
      },
      {
        name: 'Work',
        type: 'folder',
        children: [
          { name: 'README.md', type: 'file', content: '## Work Directory\n\nContains subdirectories for `Dev`, `Devops`, and `Resources`.', modified: '2023-10-26T10:59:00Z' },
          {
            name: 'Dev',
            type: 'folder',
            children: [
              {
                name: 'Projects',
                type: 'folder',
                children: [
                  { name: 'Throttler', type: 'folder', children: [], modified: '2023-10-20T09:00:00Z' },
                  { name: 'Throttler.magnet', type: 'file', content: '', modified: '2023-10-20T09:00:00Z' },
                  { name: 'Atomix', type: 'folder', children: [], modified: '2023-10-18T16:20:00Z' },
                  { name: 'Atomix.magnet', type: 'file', content: '', modified: '2023-10-18T16:20:00Z' },
                ],
                modified: '2023-10-20T09:00:00Z'
              },
              {
                name: 'Users',
                type: 'folder',
                children: [
                  { name: 'j.doe', type: 'folder', children: [], modified: '2023-08-01T12:00:00Z' },
                  { name: 's.smith', type: 'folder', children: [], modified: '2023-08-02T15:00:00Z' },
                ],
                modified: '2023-08-02T15:00:00Z'
              }
            ],
            modified: '2023-10-20T09:00:00Z'
          },
          {
            name: 'Devops',
            type: 'folder',
            children: [
              { name: 'ci-pipeline.yml', type: 'file', content: 'YAML content', modified: '2023-10-24T11:45:00Z' },
              { name: 'deploy-script.sh', type: 'file', content: 'Shell script', modified: '2023-10-23T09:30:00Z' },
            ],
            modified: '2023-10-24T11:45:00Z'
          },
          {
            name: 'Resources',
            type: 'folder',
            children: [
              { name: 'brand-assets.zip', type: 'file', content: 'zip data', modified: '2023-07-30T10:00:00Z' },
              { name: 'font-license.txt', type: 'file', content: 'License info', modified: '2023-07-29T10:00:00Z' },
            ],
            modified: '2023-07-30T10:00:00Z'
          },
        ],
        modified: '2023-10-26T11:00:00Z'
      },
      {
        name: 'Desktop',
        type: 'folder',
        children: [
          { name: 'screenshot.png', type: 'file', content: 'image data', modified: '2023-10-27T13:00:00Z' },
        ],
        modified: '2023-10-27T13:00:00Z'
      },
      {
        name: 'Home',
        type: 'folder',
        children: [],
        modified: '2023-08-01T09:00:00Z'
      },
      { 
        name: 'README.txt', 
        type: 'file', 
        content: 'This is a virtual file system stored in your browser\'s local storage.', 
        modified: '2023-08-01T09:00:00Z' 
      }
    ],
    modified: new Date().toISOString()
  });

  constructor() {
    this.loadTreeFromStorage();
    // This effect will automatically save the tree to localStorage whenever it changes.
    effect(() => {
      this.saveTreeToStorage();
    });
  }

  private loadTreeFromStorage(): void {
    try {
      const storedTree = localStorage.getItem(SESSION_FS_STORAGE_KEY);
      const sessionName = this.localConfigService.sessionName();
      if (storedTree) {
        const parsedTree: FileSystemNode = JSON.parse(storedTree);
        // Basic validation and overwrite name with config
        if (parsedTree && parsedTree.type === 'folder') {
          parsedTree.name = sessionName;
          this.rootNode.set(parsedTree);
          return;
        }
      }
      // If nothing in storage, save the default tree with the correct name
      this.rootNode.update(root => ({ ...root, name: sessionName }));
      this.saveTreeToStorage();

    } catch (e) {
      console.error('Failed to load session file system from storage.', e);
    }
  }

  private saveTreeToStorage(): void {
    try {
      localStorage.setItem(SESSION_FS_STORAGE_KEY, JSON.stringify(this.rootNode()));
    } catch (e) {
      console.error('Failed to save session file system to storage.', e);
    }
  }

  /**
   * Recursively traverses the tree to find a node at a given path.
   * NOTE: This should be used on a specific tree instance (like a clone), not on the signal directly.
   */
  private findNodeInTree(root: FileSystemNode, path: string[]): FileSystemNode | null {
    let currentNode: FileSystemNode | null = root;
    for (const segment of path) {
      const nextNode: FileSystemNode | undefined = currentNode?.children?.find(c => c.name === segment);
      if (!nextNode || nextNode.type !== 'folder') return null; // Only traverse folders
      currentNode = nextNode;
    }
    return currentNode;
  }

  private getFullPath(path: string[]): string[] {
    return [this.localConfigService.sessionName(), ...path];
  }

  async getFolderTree(): Promise<FileSystemNode> {
    const tree = cloneNode(this.rootNode());
    // Ensure the tree name is always up-to-date with the latest config
    tree.name = this.localConfigService.sessionName();
    return tree;
  }

  async getContents(path: string[]): Promise<FileSystemNode[]> {
    const node = this.findNodeInTree(this.rootNode(), path);
    if (!node || node.type !== 'folder' || !node.children) {
      throw new Error('Path not found or is not a folder.');
    }

    const magnetFiles = new Set(
      node.children
        .filter(item => item.name.endsWith('.magnet') && item.type === 'file')
        .map(item => item.name)
    );

    const processedItems: FileSystemNode[] = [];

    for (const item of node.children) {
      if (item.name.endsWith('.magnet') && item.type === 'file') {
        continue; // Skip magnet files themselves
      }
      
      const nodeClone = cloneNode(item);

      if (nodeClone.type === 'folder') {
        const magnetFileName = `${nodeClone.name}.magnet`;
        if (magnetFiles.has(magnetFileName)) {
          nodeClone.isMagnet = true;
          nodeClone.magnetFile = magnetFileName;
        }
      }
      
      processedItems.push(nodeClone);
    }
    return processedItems;
  }

  async getFileContent(path: string[], name: string): Promise<string> {
    const parentNode = this.findNodeInTree(this.rootNode(), path);
    const fileNode = parentNode?.children?.find(c => c.name === name && c.type === 'file');
    if (fileNode) {
      return fileNode.content ?? '';
    }
    throw new Error('File not found in Session file system.');
  }

  async saveFileContent(path: string[], name: string, content: string): Promise<void> {
    this.rootNode.update(root => {
      const newRoot = cloneNode(root);
      const parentNode = this.findNodeInTree(newRoot, path);
      if (!parentNode?.children) throw new Error('Path not found.');

      const fileIndex = parentNode.children.findIndex(c => c.name === name && c.type === 'file');
      if (fileIndex === -1) throw new Error(`File '${name}' not found.`);

      const updatedFile = {
        ...parentNode.children[fileIndex],
        content: content,
        modified: new Date().toISOString()
      };

      parentNode.children[fileIndex] = updatedFile;
      parentNode.modified = new Date().toISOString();
      
      return newRoot;
    });
  }

  async hasFile(path: string[], fileName: string): Promise<boolean> {
    const parentNode = this.findNodeInTree(this.rootNode(), path);
    if (!parentNode || !parentNode.children) {
      return false;
    }
    return parentNode.children.some(child => child.name === fileName && child.type === 'file');
  }

  async hasFolder(path: string[], folderName: string): Promise<boolean> {
    const parentNode = this.findNodeInTree(this.rootNode(), path);
    if (!parentNode || !parentNode.children) {
      return false;
    }
    return parentNode.children.some(child => child.name === folderName && child.type === 'folder');
  }

  async createDirectory(path: string[], name: string): Promise<void> {
    this.rootNode.update(root => {
      const newRoot = cloneNode(root);
      const parentNode = this.findNodeInTree(newRoot, path);

      if (!parentNode || parentNode.type !== 'folder') {
        throw new Error('Path not found or is not a folder.');
      }

      const currentChildren = parentNode.children ?? [];
      if (currentChildren.some(c => c.name === name)) {
        throw new Error(`An item named '${name}' already exists.`);
      }

      const newFolder: FileSystemNode = { name, type: 'folder', children: [], modified: new Date().toISOString() };
      parentNode.children = [...currentChildren, newFolder];
      parentNode.modified = new Date().toISOString();
      
      return newRoot;
    });
  }

  async createFile(path: string[], name: string): Promise<void> {
    this.rootNode.update(root => {
      const newRoot = cloneNode(root);
      const parentNode = this.findNodeInTree(newRoot, path);

      if (!parentNode || parentNode.type !== 'folder') {
        throw new Error('Path not found or is not a folder.');
      }
      
      const currentChildren = parentNode.children ?? [];
      if (currentChildren.some(c => c.name === name)) {
        throw new Error(`An item named '${name}' already exists.`);
      }

      const newFile: FileSystemNode = { name, type: 'file', content: '', modified: new Date().toISOString() };
      parentNode.children = [...currentChildren, newFile];
      parentNode.modified = new Date().toISOString();
      
      return newRoot;
    });
  }

  async removeDirectory(path: string[], name: string): Promise<void> {
    this.rootNode.update(root => {
      const newRoot = cloneNode(root);
      const parentNode = this.findNodeInTree(newRoot, path);
      if (!parentNode?.children) throw new Error('Path not found.');

      const childExists = parentNode.children.some(c => c.name === name && c.type === 'folder');
      if (!childExists) throw new Error(`Directory '${name}' not found.`);
      
      // Also remove the associated .magnet file if it exists
      const magnetFileName = `${name}.magnet`;
      parentNode.children = parentNode.children.filter(c => c.name !== name && c.name !== magnetFileName);
      parentNode.modified = new Date().toISOString();
      
      return newRoot;
    });

    // Also remove notes for this directory and its children
    const fullPath = this.getFullPath([...path, name]);
    const pathStr = fullPath.join('/');
    const allNotes = await this.dbService.getAllNotes();
    for (const note of allNotes) {
      if (note.path === pathStr || note.path.startsWith(pathStr + '/')) {
        await this.dbService.deleteNote(note.path);
      }
    }
  }

  async deleteFile(path: string[], name: string): Promise<void> {
    this.rootNode.update(root => {
      const newRoot = cloneNode(root);
      const parentNode = this.findNodeInTree(newRoot, path);
      if (!parentNode?.children) throw new Error('Path not found.');

      const childExists = parentNode.children.some(c => c.name === name && c.type === 'file');
      if (!childExists) throw new Error(`File '${name}' not found.`);
      
      parentNode.children = parentNode.children.filter(c => c.name !== name);
      parentNode.modified = new Date().toISOString();
      
      return newRoot;
    });
  }

  async rename(path: string[], oldName: string, newName: string): Promise<void> {
    this.rootNode.update(root => {
      const newRoot = cloneNode(root);
      const parentNode = this.findNodeInTree(newRoot, path);
      if (!parentNode?.children) throw new Error('Path not found.');

      if (oldName !== newName && parentNode.children.some(c => c.name === newName)) {
        throw new Error(`An item named '${newName}' already exists.`);
      }
      
      const childToRename = parentNode.children.find(c => c.name === oldName);
      if (!childToRename) throw new Error(`Item '${oldName}' not found.`);

      const newChildren = [...parentNode.children];
      const childIndex = newChildren.findIndex(c => c.name === oldName);
      if (childIndex > -1) {
          newChildren[childIndex] = { ...newChildren[childIndex], name: newName, modified: new Date().toISOString() };
      }

      // If the renamed item was a folder, rename its .magnet file too
      if (childToRename.type === 'folder') {
          const magnetFileIndex = newChildren.findIndex(c => c.name === `${oldName}.magnet`);
          if (magnetFileIndex > -1) {
              newChildren[magnetFileIndex] = { ...newChildren[magnetFileIndex], name: `${newName}.magnet`, modified: new Date().toISOString() };
          }
      }

      parentNode.children = newChildren;
      parentNode.modified = new Date().toISOString();
      return newRoot;
    });

    // Rename notes
    const oldFullPath = this.getFullPath([...path, oldName]);
    const newFullPath = this.getFullPath([...path, newName]);
    await this.renameNotes(oldFullPath.join('/'), newFullPath.join('/'));
  }

  private async renameNotes(oldPathPrefix: string, newPathPrefix: string): Promise<void> {
    const allNotes = await this.dbService.getAllNotes();
    for (const note of allNotes) {
      if (note.path === oldPathPrefix) {
        await this.dbService.deleteNote(oldPathPrefix);
        await this.dbService.saveNote({ path: newPathPrefix, content: note.content });
      } else if (note.path.startsWith(oldPathPrefix + '/')) {
        const newPath = newPathPrefix + note.path.substring(oldPathPrefix.length);
        await this.dbService.deleteNote(note.path);
        await this.dbService.saveNote({ path: newPath, content: note.content });
      }
    }
  }

  async move(sourcePath: string[], destPath: string[], items: ItemReference[]): Promise<void> {
    const movedFolders: { oldPath: string[], newPath: string[] }[] = [];
    this.rootNode.update(currentRoot => {
      const newRoot = cloneNode(currentRoot);
      const sourceParent = this.findNodeInTree(newRoot, sourcePath);
      if (!sourceParent?.children) {
        throw new Error('Source path not found.');
      }

      const destParent = this.findNodeInTree(newRoot, destPath);
      if (!destParent || destParent.type !== 'folder') {
        throw new Error('Destination path not found.');
      }

      const itemsToMove: FileSystemNode[] = [];
      const newSourceChildren: FileSystemNode[] = [];

      for (const child of sourceParent.children) {
        const shouldMove = items.some(itemRef => itemRef.name === child.name && itemRef.type === child.type);
        if (shouldMove) {
          itemsToMove.push(child);
          if (child.type === 'folder') {
            movedFolders.push({
              oldPath: this.getFullPath([...sourcePath, child.name]),
              newPath: this.getFullPath([...destPath, child.name]),
            });
            // Also look for and move the associated .magnet file
            const magnetFile = sourceParent.children.find(c => c.name === `${child.name}.magnet`);
            if (magnetFile) {
              itemsToMove.push(magnetFile);
            }
          }
        } else {
          newSourceChildren.push(child);
        }
      }

      if (itemsToMove.length === 0) {
        return currentRoot; // No change
      }
      
      if (!destParent.children) {
        destParent.children = [];
      }

      for (const item of itemsToMove) {
        if (destParent.children.some(c => c.name === item.name)) {
          throw new Error(`An item named '${item.name}' already exists in the destination.`);
        }
      }

      sourceParent.children = newSourceChildren;
      sourceParent.modified = new Date().toISOString();

      destParent.children.push(...itemsToMove);
      destParent.modified = new Date().toISOString();

      return newRoot;
    });

    // Rename notes for all moved folders
    for (const { oldPath, newPath } of movedFolders) {
      await this.renameNotes(oldPath.join('/'), newPath.join('/'));
    }
  }

  private getUniqueCopyName(originalName: string, existingChildren: FileSystemNode[]): string {
    let finalName = originalName;
    if (!existingChildren.some(c => c.name === finalName)) {
      return finalName;
    }

    let copyIndex = 1;
    const extension = originalName.includes('.') ? originalName.substring(originalName.lastIndexOf('.')) : '';
    const baseName = extension ? originalName.substring(0, originalName.lastIndexOf('.')) : originalName;

    do {
      const suffix = ` - Copy${copyIndex > 1 ? ` (${copyIndex})` : ''}`;
      finalName = `${baseName}${suffix}${extension}`;
      copyIndex++;
    } while (existingChildren.some(c => c.name === finalName));
    
    return finalName;
  }
  
  async copy(sourcePath: string[], destPath: string[], items: ItemReference[]): Promise<void> {
    this.rootNode.update(root => {
        const newRoot = cloneNode(root);

        const sourceParent = this.findNodeInTree(newRoot, sourcePath);
        if (!sourceParent?.children) {
            throw new Error('Copy failed: Source path not found.');
        }

        const destParent = this.findNodeInTree(newRoot, destPath);
        if (!destParent || destParent.type !== 'folder') {
            throw new Error('Copy failed: Destination path is not a valid folder.');
        }

        const nodesToProcess = sourceParent.children.filter(child => 
            items.some(itemRef => itemRef.name === child.name && itemRef.type === child.type)
        );

        if (nodesToProcess.length === 0) {
            return root; // No change needed
        }
        
        const nodesToCopy: FileSystemNode[] = [];
        for (const node of nodesToProcess) {
          nodesToCopy.push(node);
          // If it's a folder, also grab its .magnet file
          if (node.type === 'folder') {
            const magnetFile = sourceParent.children.find(c => c.name === `${node.name}.magnet`);
            if (magnetFile) {
              nodesToCopy.push(magnetFile);
            }
          }
        }

        const existingDestChildren = [...(destParent.children || [])];
        const newClonesToAdd: FileSystemNode[] = [];

        for (const item of nodesToCopy) {
            const itemCopy = cloneNode(item);
            const combinedChildrenCheck = [...existingDestChildren, ...newClonesToAdd];
            itemCopy.name = this.getUniqueCopyName(item.name, combinedChildrenCheck);
            itemCopy.modified = new Date().toISOString();
            newClonesToAdd.push(itemCopy);
        }
        
        if (!destParent.children) {
            destParent.children = [];
        }
        destParent.children.push(...newClonesToAdd);
        destParent.modified = new Date().toISOString();

        return newRoot;
    });
  }

  // Not supported for this in-memory provider.
  uploadFile(path: string[], file: File): Promise<void> {
    return this.createFile(path, file.name);
  }

  async importTree(destPath: string[], data: FileSystemNode): Promise<void> {
    this.rootNode.update(currentRoot => {
      const newRoot = cloneNode(currentRoot);
      const destNode = this.findNodeInTree(newRoot, destPath);
  
      if (!destNode || destNode.type !== 'folder') {
        throw new Error('Import failed: Destination path is not a valid folder.');
      }
      
      const merge = (targetNode: FileSystemNode, sourceNode: FileSystemNode) => {
        if (sourceNode.type !== 'folder') return;
        if (!targetNode.children) {
          targetNode.children = [];
        }
  
        let existingChild = targetNode.children.find(c => c.name === sourceNode.name && c.type === 'folder');
  
        if (existingChild) {
          if (sourceNode.children) {
            sourceNode.children.forEach(sourceChild => merge(existingChild!, sourceChild));
          }
        } else {
          const clonedSource = cloneNode(sourceNode);
          const setModifiedDates = (node: FileSystemNode) => {
              node.modified = new Date().toISOString();
              if (node.children) node.children.forEach(setModifiedDates);
          };
          setModifiedDates(clonedSource);
          targetNode.children.push(clonedSource);
        }
      };
  
      merge(destNode, data);
      destNode.modified = new Date().toISOString();
  
      return newRoot;
    });
  }

  async getNote(providerPath: string[]): Promise<string | undefined> {
    const fullPath = this.getFullPath(providerPath);
    const note = await this.dbService.getNote(fullPath.join('/'));
    return note?.content;
  }

  async saveNote(providerPath: string[], content: string): Promise<void> {
    const fullPath = this.getFullPath(providerPath);
    await this.dbService.saveNote({ path: fullPath.join('/'), content });
  }

  async search(path: string[], query: string): Promise<SearchResultNode[]> {
    const root = this.rootNode();
    const startNode = this.findNodeInTree(root, path);

    if (!startNode) {
      return [];
    }

    const results: SearchResultNode[] = [];
    const lowerCaseQuery = query.toLowerCase();

    const recursiveSearch = (currentNode: FileSystemNode, currentPath: string[]) => {
      // Check if current node's name matches
      if (currentNode.name.toLowerCase().includes(lowerCaseQuery)) {
        results.push({
          ...cloneNode(currentNode),
          path: currentPath, // path of parent
        });
      }
      
      // Recurse into children
      if (currentNode.type === 'folder' && currentNode.children) {
        const nextPath = [...currentPath, currentNode.name];
        for (const child of currentNode.children) {
          recursiveSearch(child, nextPath);
        }
      }
    };

    if (startNode.children) {
      for (const child of startNode.children) {
        recursiveSearch(child, path);
      }
    }
    
    return results;
  }
}