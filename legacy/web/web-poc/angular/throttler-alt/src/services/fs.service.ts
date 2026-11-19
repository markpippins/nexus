import { Injectable, inject } from '@angular/core';
import { BrokerService } from './broker.service.js';
import { FileSystemNode } from '../models/file-system.model.js';
import { ItemReference } from './file-system-provider.js';

const SERVICE_NAME = 'restFsService';

@Injectable({
  providedIn: 'root'
})
export class FsService {
  private brokerService = inject(BrokerService);

  private constructBrokerUrl(baseUrl: string): string {
    let fullUrl = baseUrl.trim();
    if (!fullUrl.startsWith('http://') && !fullUrl.startsWith('https://')) {
        fullUrl = `http://${fullUrl}`;
    }
    if (fullUrl.endsWith('/')) {
        fullUrl = fullUrl.slice(0, -1);
    }
    fullUrl += '/api/broker/submitRequest';
    return fullUrl;
  }

  listFiles(brokerUrl: string, path: string[]): Promise<FileSystemNode[]> {
    return this.brokerService.submitRequest<FileSystemNode[]>(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'listFiles', { path });
  }

  async getFileContent(brokerUrl: string, path: string[], filename: string): Promise<string> {
    const response = await this.brokerService.submitRequest<{ content: string }>(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'readFile', { path, filename });
    return response.content;
  }

  saveFileContent(brokerUrl: string, path: string[], filename: string, content: string): Promise<void> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'saveFile', { path, filename, content });
  }

  changeDirectory(brokerUrl: string, path: string[]): Promise<any> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'changeDirectory', { path });
  }

  createDirectory(brokerUrl: string, path: string[]): Promise<any> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'createDirectory', { path });
  }

  removeDirectory(brokerUrl: string, path: string[]): Promise<any> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'removeDirectory', { path });
  }

  createFile(brokerUrl: string, path: string[], filename: string): Promise<any> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'createFile', { path, filename });
  }

  deleteFile(brokerUrl: string, path: string[], filename: string): Promise<any> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'deleteFile', { path, filename });
  }

  rename(brokerUrl: string, fromPath: string[], toPath: string[]): Promise<any> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'rename', { fromPath, toPath });
  }

  hasFile(brokerUrl: string, path: string[], fileName: string): Promise<boolean> {
    return this.brokerService.submitRequest<boolean>(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'hasFile', { path, fileName });
  }

  hasFolder(brokerUrl: string, path: string[], folderName: string): Promise<boolean> {
    return this.brokerService.submitRequest<boolean>(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'hasFolder', { path, folderName });
  }

  move(brokerUrl: string, sourcePath: string[], destPath: string[], items: ItemReference[]): Promise<void> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'moveItems', { sourcePath, destPath, items });
  }

  copy(brokerUrl: string, fromPath: string[], toPath: string[]): Promise<void> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'copy', { fromPath, toPath });
  }

  async getNote(brokerUrl: string, path: string[]): Promise<{ content: string }> {
    return this.brokerService.submitRequest<{ content: string }>(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'getNote', { path });
  }

  saveNote(brokerUrl: string, path: string[], content: string): Promise<void> {
    return this.brokerService.submitRequest(this.constructBrokerUrl(brokerUrl), SERVICE_NAME, 'saveNote', { path, content });
  }
}
