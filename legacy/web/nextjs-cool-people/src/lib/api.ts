import type { ApiResponse } from './types';

interface BrokerRequest {
  service: string;
  operation: string;
  params: Record<string, any>;
  requestId: string;
}

// Generic Broker Call
async function callBrokerApi(service: string, operation: string, params: Record<string, any>): Promise<ApiResponse> {
  try {
    // Generate a unique request ID
    const requestId = typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `req-${Date.now()}`;

    const brokerPayload: BrokerRequest = {
      service,
      operation,
      params,
      requestId
    };

    // We use the internal API route as a proxy to the backend
    // This works for both client-side and server-side (if base URL is handled)
    // For server-side calls, we might want to call the backend directly if possible, 
    // but using the proxy ensures consistency. 
    // However, 'fetch' in Server Components needs an absolute URL if calling localhost.
    // Better: If running on server, call backend directly? 
    // Actually, let's use the env var for backend URL if on server.

    let response;
    if (typeof window === 'undefined') {
      // Server-side: Call Broker directly
      const backendUrl = process.env.BROKER_SERVICE_URL || 'http://localhost:8080/api/broker/submitRequest';
      response = await fetch(backendUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(brokerPayload)
      });
    } else {
      // Client-side: Call Next.js API proxy
      response = await fetch('/api/broker/submitRequest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(brokerPayload)
      });
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: 'Network response was not ok' }));
      throw new Error(errorData.detail || errorData.error || `HTTP error! status: ${response.status}`);
    }

    const brokerResponse = await response.json();

    if (!brokerResponse.ok) {
      const errorMessages = brokerResponse.errors?.map((e: any) => e.message).join(', ') || 'Broker operation failed';
      throw new Error(errorMessages);
    }

    return brokerResponse.data || { ok: true };
  } catch (error) {
    console.error(`Broker call failed [${service}.${operation}]:`, error);
    if (error instanceof Error) {
      return { ok: false, error: error.message };
    }
    return { ok: false, error: 'An unknown error occurred' };
  }
}

// --- File System Service ---
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


// --- User Service ---
export const getUser = (token: string, userId: string): Promise<ApiResponse> =>
  callBrokerApi('userService', 'findById', { token, id: userId });

export const getUserByUsername = (token: string, username: string): Promise<ApiResponse> =>
  callBrokerApi('userService', 'findByAlias', { token, alias: username });


// --- Forum Service ---
export const getForums = (token: string): Promise<ApiResponse> =>
  callBrokerApi('forumService', 'findAll', { token });

export const getForumBySlug = (token: string, slug: string): Promise<ApiResponse> =>
  callBrokerApi('forumService', 'findByName', { token, name: slug }); // Assuming slug maps to name or we need a findBySlug


// --- Post Service ---
export const getPosts = (token: string): Promise<ApiResponse> =>
  callBrokerApi('postService', 'findAll', { token });

export const getPostsByUser = (token: string, userId: string): Promise<ApiResponse> =>
  callBrokerApi('postService', 'findByUser', { token, userId });

export const savePost = (token: string, post: any): Promise<ApiResponse> =>
  callBrokerApi('postService', 'save', { token, post });

export const deletePost = (token: string, postId: string): Promise<ApiResponse> =>
  callBrokerApi('postService', 'delete', { token, postId });


// --- Comment Service ---
export const getCommentsForPost = (token: string, postId: string): Promise<ApiResponse> =>
  callBrokerApi('commentService', 'findCommentsForPost', { token, postId });

export const addComment = (token: string, comment: any): Promise<ApiResponse> =>
  callBrokerApi('commentService', 'addComment', { token, comment });