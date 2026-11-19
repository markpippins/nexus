import { Injectable, signal, inject } from '@angular/core';
import { DbService } from './db.service.js';
import { FolderProperties } from '../models/folder-properties.model.js';

@Injectable({
  providedIn: 'root',
})
export class FolderPropertiesService {
  private dbService = inject(DbService);
  private propertiesMap = signal<Map<string, FolderProperties>>(new Map());

  constructor() {
    this.loadProperties();
  }

  private async loadProperties(): Promise<void> {
    const allProps = await this.dbService.getAllFolderProperties();
    const map = new Map<string, FolderProperties>();
    for (const prop of allProps) {
      map.set(prop.path, prop);
    }
    this.propertiesMap.set(map);
  }

  getProperties(path: string[]): FolderProperties | undefined {
    const pathStr = path.join('/');
    return this.propertiesMap().get(pathStr);
  }

  async updateProperties(path: string[], props: Partial<Omit<FolderProperties, 'path'>>): Promise<void> {
    const pathStr = path.join('/');
    const existing = this.propertiesMap().get(pathStr) ?? { path: pathStr };
    
    // Filter out empty strings, treating them as "unset"
    const displayName = props.displayName?.trim() ? props.displayName.trim() : undefined;
    const imageName = props.imageName?.trim() ? props.imageName.trim() : undefined;

    const updatedProps: FolderProperties = { ...existing, displayName, imageName };

    // If no custom properties are left, remove the entry
    if (!updatedProps.displayName && !updatedProps.imageName) {
      this.propertiesMap.update(map => {
        const newMap = new Map(map);
        newMap.delete(pathStr);
        return newMap;
      });
      await this.dbService.deleteFolderProperties(pathStr);
    } else {
      this.propertiesMap.update(map => new Map(map).set(pathStr, updatedProps));
      await this.dbService.updateFolderProperties(updatedProps);
    }
  }

  async handleRename(oldPath: string[], newPath: string[]): Promise<void> {
    const oldPathStr = oldPath.join('/');
    const newPathStr = newPath.join('/');
    const newMap = new Map(this.propertiesMap());
    let hasChanges = false;

    for (const [pathStr, props] of this.propertiesMap().entries()) {
      if (pathStr === oldPathStr) {
        const newProps = { ...props, path: newPathStr };
        newMap.delete(oldPathStr);
        newMap.set(newPathStr, newProps);
        await this.dbService.deleteFolderProperties(oldPathStr);
        await this.dbService.updateFolderProperties(newProps);
        hasChanges = true;
      } else if (pathStr.startsWith(oldPathStr + '/')) {
        const subPath = pathStr.substring(oldPathStr.length);
        const newFullPathStr = newPathStr + subPath;
        const newProps = { ...props, path: newFullPathStr };
        newMap.delete(pathStr);
        newMap.set(newFullPathStr, newProps);
        await this.dbService.deleteFolderProperties(pathStr);
        await this.dbService.updateFolderProperties(newProps);
        hasChanges = true;
      }
    }

    if (hasChanges) {
      this.propertiesMap.set(newMap);
    }
  }

  async handleDelete(path: string[]): Promise<void> {
    const pathStr = path.join('/');
    const newMap = new Map(this.propertiesMap());
    let hasChanges = false;
    
    for (const key of this.propertiesMap().keys()) {
        if (key === pathStr || key.startsWith(pathStr + '/')) {
            newMap.delete(key);
            await this.dbService.deleteFolderProperties(key);
            hasChanges = true;
        }
    }

    if (hasChanges) {
        this.propertiesMap.set(newMap);
    }
  }
}
