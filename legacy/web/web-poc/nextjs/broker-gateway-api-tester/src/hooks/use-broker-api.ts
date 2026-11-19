'use client';

import { useBrokerUrl } from '@/contexts/broker-url-context';
import { callBrokerApiWithUrl } from '@/lib/api';
import type { ApiResponse } from '@/lib/types';

export function useBrokerApi() {
  const { brokerUrl } = useBrokerUrl();

  const callBroker = async (
    service: string, 
    operation: string, 
    params: Record<string, any>
  ): Promise<ApiResponse> => {
    return callBrokerApiWithUrl(brokerUrl, service, operation, params);
  };

  return {
    brokerUrl,
    callBroker,
    listDirectory: (token: string, path: string[]) => 
      callBroker('restFsService', 'listFiles', { token, path }),
    createDirectory: (token: string, path: string[], newDirName: string) => 
      callBroker('restFsService', 'createDirectory', { token, path, filename: newDirName }),
    createFile: (token: string, path: string[], newFileName: string) => 
      callBroker('restFsService', 'createFile', { token, path, filename: newFileName }),
    deleteItem: (token: string, path: string[], name: string, type: 'file' | 'directory') => {
      const operation = type === 'file' ? 'deleteFile' : 'removeDirectory';
      return callBroker('restFsService', operation, { token, path, filename: name });
    },
    renameItem: (token: string, path: string[], oldName: string, newName: string) => 
      callBroker('restFsService', 'rename', { token, path, filename: oldName, newName: newName }),
  };
}