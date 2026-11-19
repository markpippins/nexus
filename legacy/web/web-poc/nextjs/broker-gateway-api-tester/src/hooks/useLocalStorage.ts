'use client';

import { useState, useEffect, useCallback, useRef } from 'react';

function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T | ((val: T) => T)) => void] {
  const [storedValue, setStoredValue] = useState<T>(initialValue);
  const isClientRef = useRef(false);

  const readValue = useCallback((): T => {
    try {
      if (typeof window !== 'undefined' && isClientRef.current) {
        const item = window.localStorage.getItem(key);
        return item ? (JSON.parse(item) as T) : initialValue;
      }
      return initialValue;
    } catch (error) {
      console.warn(`Error reading localStorage key “${key}”:`, error);
      return initialValue;
    }
  }, [initialValue, key]);

  useEffect(() => {
    // Mark that we're on the client side
    isClientRef.current = true;

    // On the client, read from localStorage
    try {
      if (typeof window !== 'undefined') {
        const item = window.localStorage.getItem(key);
        if (item !== null) {
          setStoredValue(JSON.parse(item));
        }
      }
    } catch (error) {
      console.warn(`Error reading localStorage key “${key}”:`, error);
    }
  }, [key]);

  const setValue = useCallback(
    (value: T | ((val: T) => T)) => {
      try {
        // Only update localStorage if we're on the client side
        if (isClientRef.current && typeof window !== 'undefined') {
          const newValue = value instanceof Function ? value(storedValue) : value;
          window.localStorage.setItem(key, JSON.stringify(newValue));
          setStoredValue(newValue);
          window.dispatchEvent(new Event('local-storage'));
        } else {
          // If we're on the server, just update the state
          const newValue = value instanceof Function ? value(storedValue) : value;
          setStoredValue(newValue);
        }
      } catch (error) {
        console.warn(`Error setting localStorage key “${key}”:`, error);
      }
    },
    [key, storedValue]
  );

  // Update state when localStorage changes (client-side only)
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const handleStorageChange = (e: StorageEvent) => {
        if (e.key === key && e.newValue !== null) {
          try {
            setStoredValue(JSON.parse(e.newValue));
          } catch (error) {
            console.warn(`Error parsing localStorage key “${key}”:`, error);
          }
        }
      };

      window.addEventListener('storage', handleStorageChange);
      window.addEventListener('local-storage', handleStorageChange);
      return () => {
        window.removeEventListener('storage', handleStorageChange);
        window.removeEventListener('local-storage', handleStorageChange);
      };
    }
  }, [key]);

  return [storedValue, setValue];
}

export default useLocalStorage;
