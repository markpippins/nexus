// Utility functions for authentication

// Get token from browser storage (localStorage or sessionStorage)
export function getStoredToken(): string | null {
  if (typeof window !== 'undefined') {
    // Try to get token from localStorage first
    const token = localStorage.getItem('coolpeople-token') || sessionStorage.getItem('coolpeople-token');
    return token;
  }
  return null;
}

// Get user from browser storage
export function getStoredUser(): any | null {
  if (typeof window !== 'undefined') {
    const user = localStorage.getItem('coolpeople-user') || sessionStorage.getItem('coolpeople-user');
    return user ? JSON.parse(user) : null;
  }
  return null;
}

// Store token and user in browser storage
export function storeAuthData(token: string, user: any, useLocalStorage: boolean = true) {
  if (typeof window !== 'undefined') {
    const storage = useLocalStorage ? localStorage : sessionStorage;
    storage.setItem('coolpeople-token', token);
    storage.setItem('coolpeople-user', JSON.stringify(user));
  }
}

// Clear stored auth data
export function clearStoredAuthData(useLocalStorage: boolean = true) {
  if (typeof window !== 'undefined') {
    const storage = useLocalStorage ? localStorage : sessionStorage;
    storage.removeItem('coolpeople-token');
    storage.removeItem('coolpeople-user');
  }
}