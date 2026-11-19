"use client";

import { createContext, useContext, useState, ReactNode } from "react";

type NavigationSection = "admin" | "systems" | "testing" | null;

type NavigationRoute = {
  href: string;
  label: string;
  section: NavigationSection;
};

interface NavigationContextType {
  activeSection: NavigationSection;
  setActiveSection: (section: NavigationSection) => void;
  currentRoute: string;
  setCurrentRoute: (route: string) => void;
  getSectionRoutes: (section: NavigationSection) => NavigationRoute[];
}

const navigationRoutes: NavigationRoute[] = [

  { href: "/admin-file-navigator", label: "Explore", section: "admin" },
  { href: "/user-management", label: "User Management", section: "admin" },

  { href: "/user-file-navigator", label: "Explore", section: "systems" },
  { href: "/ocr-upload", label: "Upload and Review", section: 'systems', },

  // { href: "/hello-api", label: "Hello", section: "testing" },
  { href: "/broker-service", label: 'Broker Service Test', section: 'testing', },
  { href: "/create-forum-test", label: "Forum Service Test", section: "testing" },
  { href: "/create-role-test", label: "Role Service Test", section: "testing" },
  { href: "/create-user-test", label: "User Service Test", section: "testing" },
  { href: "/file-upload", label: 'Upload Service Testing', section: 'testing', },

];

const NavigationContext = createContext<NavigationContextType | undefined>(undefined);

export function NavigationProvider({ children }: { children: ReactNode }) {
  const [activeSection, setActiveSection] = useState<NavigationSection>(null);
  const [currentRoute, setCurrentRoute] = useState<string>("/");

  const getSectionRoutes = (section: NavigationSection): NavigationRoute[] => {
    return navigationRoutes.filter(route => route.section === section);
  };

  return (
    <NavigationContext.Provider
      value={{
        activeSection,
        setActiveSection,
        currentRoute,
        setCurrentRoute,
        getSectionRoutes
      }}
    >
      {children}
    </NavigationContext.Provider>
  );
}

export function useNavigation() {
  const context = useContext(NavigationContext);
  if (context === undefined) {
    throw new Error("useNavigation must be used within a NavigationProvider");
  }
  return context;
}