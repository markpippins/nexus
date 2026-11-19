import type { ApiResponse } from './types';

interface BrokerRequest {
  service: string;
  operation: string;
  params: Record<string, any>;
  requestId: string;
}

// Update API request to use tokens instead of alias
export async function callBrokerApiWithUrl(brokerUrl: string, service: string, operation: string, params: Record<string, any>): Promise<ApiResponse> {
  try {
    // Generate a unique request ID
    const requestId = crypto.randomUUID ? crypto.randomUUID() : `req-${Date.now()}`;

    const brokerPayload = {
      serviceRequest: {
        service,
        operation,
        params,
        requestId
      },
      brokerUrl
    };

    const response = await fetch('/api/broker/submitRequest', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(brokerPayload),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: 'Network response was not ok' }));
      throw new Error(errorData.detail || errorData.error || `HTTP error! status: ${response.status}`);
    }

    const brokerResponse = await response.json();

    // The broker response has a different structure than direct API response
    if (!brokerResponse.ok) {
      const errorMessages = brokerResponse.errors?.map((e: any) => e.message).join(', ') || 'Broker operation failed';
      throw new Error(errorMessages);
    }

    return brokerResponse.data || { ok: true };
  } catch (error) {
    if (error instanceof Error) {
      return { error: error.message };
    }
    return { error: 'An unknown error occurred' };
  }
}

// Legacy functions - these will need to be updated to work with the new approach
// For now, they will use a default URL
export async function callBrokerApi(service: string, operation: string, params: Record<string, any>): Promise<ApiResponse> {
  const defaultUrl = process.env.NEXT_PUBLIC_BROKER_SERVICE_URL ||
                    process.env.BROKER_SERVICE_URL ||
                    'http://localhost:8080/api/broker/submitRequest';
  return callBrokerApiWithUrl(defaultUrl, service, operation, params);
}

export const listDirectory = (token: string, path: string[]): Promise<ApiResponse> =>
  callBrokerApi('restFsService', 'listFiles', { token, path });

export const createDirectory = (token: string, path: string[], newDirName: string): Promise<ApiResponse> =>
  callBrokerApi('restFsService', 'createDirectory', { token, path, filename: newDirName });

export const createFile = (token: string, path: string[], newFileName: string): Promise<ApiResponse> =>
  callBrokerApi('restFsService', 'createFile', { token, path, filename: newFileName });

export const deleteItem = (token: string, path: string[], name: string, type: 'file' | 'directory'): Promise<ApiResponse> => {
  const operation = type === 'file' ? 'deleteFile' : 'removeDirectory';
  return callBrokerApi('restFsService', operation, { token, path, filename: name });
};

export const renameItem = (token: string, path: string[], oldName: string, newName: string): Promise<ApiResponse> =>
  callBrokerApi('restFsService', 'rename', { token, path, filename: oldName, newName: newName });
