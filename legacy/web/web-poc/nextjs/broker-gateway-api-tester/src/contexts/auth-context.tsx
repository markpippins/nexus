"use client";

import {
  createContext,
  useContext,
  useState,
  ReactNode,
  useEffect,
} from "react";
import { v4 as uuidv4 } from "uuid";
import { useNavigation } from "./navigation-context";
import { useRouter } from "next/navigation";
import { useBrokerApi } from "@/hooks/use-broker-api";

interface LoginResponse {
  token: string;
  message?: string;
  ok: boolean;
  user?: User;
  errors?: { message: string }[];
}
interface User {
  id: string;
  profileId: string;
  alias: string;
  email: string;
  avatarUrl?: string;
  token?: string;
}

interface AuthContextType {
  isLoggedIn: boolean;
  user: User | null;
  login: (alias: string, identifier: string) => Promise<void>;
  createUser: (email: string, alias: string, identifier: string) => Promise<LoginResponse>;
  logout: () => void;
  isDebugMode: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Check if debug mode is enabled from environment variables
const isDebugMode = process.env.NEXT_PUBLIC_DEBUG_MODE === 'true';
const debugUserAlias = process.env.NEXT_PUBLIC_DEBUG_USER_ALIAS || 'debug_user';
const debugUserEmail = process.env.NEXT_PUBLIC_DEBUG_USER_EMAIL || 'debug@example.com';

// Create a dummy user for debug mode
const dummyUser: User = {
  id: 'debug-user-id',
  profileId: 'debug-profile-id',
  alias: debugUserAlias,
  email: debugUserEmail,
  avatarUrl: undefined
};

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<(User & { token?: string }) | null>(isDebugMode ? dummyUser : null);
    const { setActiveSection, setCurrentRoute } = useNavigation();
    const { callBroker } = useBrokerApi();
    const router = useRouter();

  // Function to ensure user directory exists
  const ensureUserDirectoryExists = async (userAlias: string) => {
    try {
      // First, ensure the users directory exists
      const createUsersResponse = await fetch("/api/fs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: ["users"],
          operation: "mkdir",
          alias: userAlias,
        }),
      });

      // Then create the user-specific directory
      const createUserDirResponse = await fetch("/api/fs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: ["users", userAlias],
          operation: "mkdir",
          alias: userAlias,
        }),
      });

      // Finally create the home directory
      const createHomeDirResponse = await fetch("/api/fs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: ["users", userAlias, "home"],
          operation: "mkdir",
          alias: userAlias,
        }),
      });

      if (!createHomeDirResponse.ok) {
        const errorData = await createHomeDirResponse.text();
        console.error("Failed to create user home directory:", errorData);
        throw new Error(`Failed to create home directory: ${errorData}`);
      }
    } catch (error) {
      console.error("Error creating user directory:", error);
      // We don't throw here to prevent login failures, but we log the error
    }
  };

  const login = async (alias: string, identifier: string) => {
    // Skip API call if in debug mode
    if (isDebugMode) {
      console.log("Debug mode: Using dummy user instead of API login");
      // Create user directory in debug mode
      await ensureUserDirectoryExists(debugUserAlias);
      return;
    }

    const result = await callBroker("loginService", "login", { alias, identifier });

    if (result.error) {
      throw new Error(result.error);
    }

    // If the API doesn't return user data, create a minimal user object
    if (result.user) {
      setUser(result.user);
      // Create user directory after successful login
      await ensureUserDirectoryExists(result.user.alias);
    } else {
      // Create a minimal user object from the login parameters
      const minimalUser: User = {
        id: `temp-${Date.now()}`, // Temporary ID
        profileId: `profile-${Date.now()}`,
        alias: alias, // Use the alias from login parameters
        email: `${alias}@temp.com`, // Temporary email
        avatarUrl: undefined
      };
      setUser(minimalUser);
      // Create user directory after successful login
      await ensureUserDirectoryExists(alias);
    }
  };

  const createUser = async (email: string, alias: string, identifier: string) => {
    // Skip API call if in debug mode
    if (isDebugMode) {
      console.log("Debug mode: Using dummy user instead of API user creation");
      return {
        token: "debug-token",
        ok: true,
        user: dummyUser
      };
    }

    const result = await callBroker("userService", "createUser", { email, alias, identifier });

    if (result.error) {
      throw new Error(result.error);
    }

    // Return the created user data if needed
    return result;
  };

  const logout = () => {
    setUser(null);
    setActiveSection(null);
    setCurrentRoute("/");
    router.push("/");
  };

  const isLoggedIn = !!user;

  return (
    <AuthContext.Provider value={{ isLoggedIn, user, login, createUser, logout, isDebugMode }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
