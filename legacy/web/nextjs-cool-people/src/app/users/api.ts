import { User, UserFormValues } from './types';
import { getStoredToken } from '@/lib/auth-utils';

// Base URL for the broker gateway
const BROKER_GATEWAY_URL = process.env.NEXT_PUBLIC_BROKER_GATEWAY_URL || 'http://localhost:8080/api/broker/submitRequest';

/**
 * Submit a request to the broker service
 */
async function submitBrokerRequest(service: string, operation: string, params: Record<string, any> = {}) {
  // Add token to params if it exists for authenticated operations
  const token = getStoredToken();
  if (token) {
    params.token = token;
  }
  
  const response = await fetch(BROKER_GATEWAY_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      service,
      operation,
      params,
      requestId: `req-${Date.now()}`,
    }),
  });

  if (!response.ok) {
    throw new Error(`Broker request failed with status ${response.status}`);
  }

  const result = await response.json();
  
  if (!result.ok) {
    throw new Error(result.errors?.[0]?.message || 'Broker operation failed');
  }

  return result.data;
}

/**
 * Get all users from the user service
 */
export async function getAllUsers(): Promise<User[]> {
  try {
    const users = await submitBrokerRequest('userService', 'findAll');
    return users || [];
  } catch (error) {
    console.error('Error fetching users:', error);
    throw error;
  }
}

/**
 * Get a single user by ID
 */
export async function getUserById(userId: string): Promise<User> {
  try {
    return await submitBrokerRequest('userService', 'findById', { userId });
  } catch (error) {
    console.error(`Error fetching user with ID ${userId}:`, error);
    throw error;
  }
}

/**
 * Get a user by alias
 */
export async function getUserByAlias(alias: string): Promise<User> {
  try {
    return await submitBrokerRequest('userService', 'findByAlias', { alias });
  } catch (error) {
    console.error(`Error fetching user with alias ${alias}:`, error);
    throw error;
  }
}

/**
 * Get a user by email
 */
export async function getUserByEmail(email: string): Promise<User> {
  try {
    return await submitBrokerRequest('userService', 'findByEmail', { email });
  } catch (error) {
    console.error(`Error fetching user with email ${email}:`, error);
    throw error;
  }
}

/**
 * Create a new user
 */
export async function createUser(userData: UserFormValues): Promise<User> {
  try {
    return await submitBrokerRequest('userService', 'createUser', {
      email: userData.email,
      alias: userData.alias,
      identifier: userData.identifier,
    });
  } catch (error) {
    console.error('Error creating user:', error);
    throw error;
  }
}

/**
 * Update an existing user
 */
export async function updateUser(userData: UserFormValues): Promise<User> {
  try {
    // For now, we'll create a new user object since the update operation might need a different structure
    // This may need to be adjusted based on the actual API implementation
    const userForUpdate = {
      id: userData.id,
      alias: userData.alias,
      email: userData.email,
      identifier: userData.identifier,
      avatarUrl: 'https://picsum.photos/50/50' // default avatar, might need to be updated
    };
    
    return await submitBrokerRequest('userService', 'saveUser', { user: userForUpdate });
  } catch (error) {
    console.error('Error updating user:', error);
    throw error;
  }
}

/**
 * Delete a user by ID
 */
export async function deleteUser(userId: string): Promise<void> {
  try {
    await submitBrokerRequest('userService', 'deleteUser', { id: userId });
  } catch (error) {
    console.error(`Error deleting user with ID ${userId}:`, error);
    throw error;
  }
}

/**
 * Add a user using UserDTO
 */
export async function addUser(userData: User): Promise<User> {
  try {
    return await submitBrokerRequest('userService', 'addUser', { user: userData });
  } catch (error) {
    console.error('Error adding user:', error);
    throw error;
  }
}