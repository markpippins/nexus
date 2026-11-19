'use client';

import type { User } from '@/lib/types';
import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
} from 'react';
import { getSessionAction, logoutAction } from '@/lib/actions';

type AuthContextType = {
  user: User | null;
  token: string | null;
  isLoggedIn: boolean;
  login: (alias: string, identifier: string) => Promise<void>; // Kept for compatibility but warns
  logout: () => void;
  checkAuth: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);

  const checkAuth = useCallback(async () => {
    try {
      // Check server-side session (cookies)
      const session = await getSessionAction();
      if (session.token && session.user) {
        setToken(session.token);
        setUser(session.user);
      } else {
        setToken(null);
        setUser(null);
      }
    } catch (e) {
      console.error("Auth check failed", e);
      setToken(null);
      setUser(null);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  const login = useCallback(async (alias: string, identifier: string) => {
    console.warn("Client-side login is deprecated. Use Server Actions.");
    // We could redirect to login page or implement calling the server action here, 
    // but typically login is done via the form.
  }, []);

  const logout = useCallback(async () => {
    await logoutAction();
    setUser(null);
    setToken(null);
    window.location.href = '/'; // Hard refresh to clear server state
  }, []);

  const isLoggedIn = !!token && !!user;

  return (
    <AuthContext.Provider value={{ user, token, isLoggedIn, login, logout, checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
