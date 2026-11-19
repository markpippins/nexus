import { FileSystemProvider, ItemReference } from './file-system-provider.js';
import { FileSystemNode, SearchResultNode } from '../models/file-system.model.js';
import { FsService } from './fs.service.js';
import { ServerProfile } from '../models/server-profile.model.js';
import { SearchService } from './search.service.js';

export class RemoteFileSystemService implements FileSystemProvider {
  constructor(
    public readonly profile: ServerProfile,
    private fsService: FsService,
    private searchService: SearchService | null,
    private userAlias: string | null
  ) {}

  async getContents(path: string[]): Promise<FileSystemNode[]> {
    const response: any = await this.fsService.listFiles(this.profile.brokerUrl, path);

    let rawItems: any[] = [];

    // First, unwrap the array from the response object
    if (Array.isArray(response)) {
      rawItems = response;
    } else if (response && typeof response === 'object') {
      if (Array.isArray(response.files)) {
        rawItems = response.files;
      } else if (Array.isArray(response.items)) {
        rawItems = response.items;
      }
    }

    if (!rawItems.length && response) {
      if (!Array.isArray(response) && !response.files && !response.items) {
        console.error('Unexpected response structure from file system API:', response);
      }
    }

    const magnetFiles = new Set(
      rawItems
        .filter(item => item.name && item.name.endsWith('.magnet'))
        .map(item => item.name)
    );

    const processedItems: FileSystemNode[] = [];

    for (const item of rawItems) {
      if (item.name && item.name.endsWith('.magnet')) {
        continue; // Skip magnet files themselves
      }
      
      const itemType = (item.type || '').toLowerCase();
      const isFolder = itemType === 'folder' || itemType === 'directory';

      const node: FileSystemNode = {
        name: item.name,
        type: isFolder ? 'folder' : 'file',
        modified: item.modified,
        content: item.content,
      };

      if (isFolder) {
        const magnetFileName = `${item.name}.magnet`;
        if (magnetFiles.has(magnetFileName)) {
          node.isMagnet = true;
          node.magnetFile = magnetFileName;
        }
      }
      
      processedItems.push(node);
    }
    return processedItems;
  }

  getFileContent(path: string[], name: string): Promise<string> {
    return this.fsService.getFileContent(this.profile.brokerUrl, path, name);
  }

  saveFileContent(path: string[], name: string, content: string): Promise<void> {
    return this.fsService.saveFileContent(this.profile.brokerUrl, path, name, content);
  }

  async getFolderTree(): Promise<FileSystemNode> {
    const topLevelItems = await this.getContents([]);
    const children = topLevelItems.map((item): FileSystemNode => {
      if (item.type === 'folder') {
        return {
          ...item,
          children: [],
          childrenLoaded: false, // Mark for lazy loading
        };
      }
      return item;
    });

    return {
      name: this.profile.name,
      type: 'folder',
      children: children,
      childrenLoaded: true, // The root's direct children are now loaded
    };
  }

  hasFile(path: string[], fileName: string): Promise<boolean> {
    return this.fsService.hasFile(this.profile.brokerUrl, path, fileName);
  }

  hasFolder(path: string[], folderName: string): Promise<boolean> {
    return this.fsService.hasFolder(this.profile.brokerUrl, path, folderName);
  }

  createDirectory(path: string[], name: string): Promise<void> {
    return this.fsService.createDirectory(this.profile.brokerUrl, [...path, name]);
  }

  async removeDirectory(path: string[], name: string): Promise<void> {
    // First, remove the directory
    await this.fsService.removeDirectory(this.profile.brokerUrl, [...path, name]);
  
    // Now, check for and remove the associated .magnet file
    const magnetFileName = `${name}.magnet`;
    const fileExists = await this.hasFile(path, magnetFileName);
    if (fileExists) {
      await this.fsService.deleteFile(this.profile.brokerUrl, path, magnetFileName);
    }
  }

  createFile(path: string[], name: string): Promise<void> {
    return this.fsService.createFile(this.profile.brokerUrl, path, name);
  }

  deleteFile(path: string[], name: string): Promise<void> {
    return this.fsService.deleteFile(this.profile.brokerUrl, path, name);
  }

  async rename(path: string[], oldName: string, newName: string): Promise<void> {
    const fromPath = [...path, oldName];
    const toPath = [...path, newName];
    
    // First, rename the primary item
    await this.fsService.rename(this.profile.brokerUrl, fromPath, toPath);
    
    // Now, check for and rename the associated .magnet file
    const magnetFileName = `${oldName}.magnet`;
    const fileExists = await this.hasFile(path, magnetFileName);
    if (fileExists) {
      const newMagnetFileName = `${newName}.magnet`;
      // The rename operation in fs.service takes full paths for 'from' and 'to'
      await this.fsService.rename(this.profile.brokerUrl, [...path, magnetFileName], [...path, newMagnetFileName]);
    }
  }

  uploadFile(path: string[], file: File): Promise<void> {
    console.warn(`File upload not implemented in live mode. File: ${file.name}, Path: ${path.join('/')}`);
    return Promise.resolve();
  }

  move(sourcePath: string[], destPath: string[], items: ItemReference[]): Promise<void> {
    return this.fsService.move(this.profile.brokerUrl, sourcePath, destPath, items);
  }

  async copy(sourcePath: string[], destPath: string[], items: ItemReference[]): Promise<void> {
    const copyPromises = items.map(item => {
        const fromPath = [...sourcePath, item.name];
        const toPath = [...destPath, item.name];
        return this.fsService.copy(this.profile.brokerUrl, fromPath, toPath);
    });
    
    await Promise.all(copyPromises);
  }

  async getNote(path: string[]): Promise<string | undefined> {
    const response = await this.fsService.getNote(this.profile.brokerUrl, path);
    return response.content;
  }

  saveNote(path: string[], content: string): Promise<void> {
    return this.fsService.saveNote(this.profile.brokerUrl, path, content);
  }

  importTree(destPath: string[], data: FileSystemNode): Promise<void> {
    return Promise.reject(new Error('Import operation is not supported for remote file systems.'));
  }

  async search(path: string[], query: string): Promise<SearchResultNode[]> {
    if (!this.searchService || !this.profile.searchUrl || !this.userAlias) {
      return Promise.reject(new Error('Search is not configured or you are not logged in for this remote connection.'));
    }
    return this.searchService.search(this.profile.searchUrl, this.userAlias, path, query);
  }
}