import { useState, useEffect, useRef } from 'react';

/**
 * A hook that safely handles localStorage access, ensuring it only runs on the client side
 */
export function useSafeLocalStorage(key: string, initialValue: string) {
  const [storedValue, setStoredValue] = useState<string>(initialValue);
  const isClientRef = useRef(false);

  useEffect(() => {
    // Mark that we're on the client side
    isClientRef.current = true;

    // On the client, read from localStorage
    try {
      if (typeof window !== 'undefined') {
        const item = window.localStorage.getItem(key);
        if (item !== null) {
          setStoredValue(item);
        }
      }
    } catch (error) {
      console.warn(`Error reading localStorage key “${key}”:`, error);
    }
  }, [key]);

  const setValue = (value: string) => {
    try {
      // Only update localStorage if we're on the client side
      if (isClientRef.current && typeof window !== 'undefined') {
        window.localStorage.setItem(key, value);
        setStoredValue(value);
      } else {
        // If we're on the server, just update the state
        setStoredValue(value);
      }
    } catch (error) {
      console.warn(`Error setting localStorage key “${key}”:`, error);
    }
  };

  // Update state when localStorage changes (client-side only)
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const handleStorageChange = (e: StorageEvent) => {
        if (e.key === key && e.newValue !== null) {
          setStoredValue(e.newValue);
        }
      };

      window.addEventListener('storage', handleStorageChange);
      return () => {
        window.removeEventListener('storage', handleStorageChange);
      };
    }
  }, [key]);

  return [storedValue, setValue] as const;
}