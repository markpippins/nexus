"use client";

import { useAuth } from "@/contexts/auth-context";
import { useNavigation } from "@/contexts/navigation-context";
import { useBrokerUrl } from "@/contexts/broker-url-context";
import { Button } from "@/components/ui/button";
import { LoginDialog } from "@/components/login-dialog";
import { CreateUserDialog } from "@/components/create-user-dialog";
import { BrokerUrlDialog } from "@/components/broker-url-dialog";
import { useRouter, usePathname } from "next/navigation";
import { useEffect } from "react";
import { Waypoints, Settings, Users, TestTube, Settings2, Server } from "lucide-react";
import { cn } from "@/lib/utils";

export function Toolbar() {
  const { isLoggedIn, user, logout } = useAuth();
  const { activeSection, setActiveSection, currentRoute, setCurrentRoute, getSectionRoutes } = useNavigation();
  const { brokerUrl } = useBrokerUrl();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    setCurrentRoute(pathname);
  }, [pathname, setCurrentRoute]);

  const handleSectionClick = (section: "admin" | "systems" | "testing") => {
    if (activeSection === section) {
      setActiveSection(null);
    } else {
      setActiveSection(section);
    }
    router.push("/");
  };

  const handleRouteClick = (href: string) => {
    setCurrentRoute(href);
    router.push(href);
  };

  const handleHomeClick = () => {
    setActiveSection(null);
    setCurrentRoute("/");
    router.push("/");
  };

  const sectionRoutes = activeSection ? getSectionRoutes(activeSection) : [];

  return (
    <div className="bg-background border-b">
      <div className="container mx-auto px-4 sm:px-8 flex items-center justify-between h-12">
        {/* Left side - Logo and main sections */}
        <div className="flex items-center gap-6">
          <button
            onClick={handleHomeClick}
            className="flex items-center gap-2 font-bold text-lg hover:text-primary transition-colors"
          >
            <Waypoints className="h-6 w-6 text-primary" />
            <span>API Tester</span>
          </button>

          {isLoggedIn && (
            <div className="flex items-center gap-2">
              <Button
                variant={activeSection === "admin" ? "default" : "ghost"}
                size="sm"
                onClick={() => handleSectionClick("admin")}
                className="flex items-center gap-2"
              >
                <Users className="h-4 w-4" />
                Admin
              </Button>
              <Button
                variant={activeSection === "systems" ? "default" : "ghost"}
                size="sm"
                onClick={() => handleSectionClick("systems")}
                className="flex items-center gap-2"
              >
                <Settings className="h-4 w-4" />
                Systems
              </Button>
              <Button
                variant={activeSection === "testing" ? "default" : "ghost"}
                size="sm"
                onClick={() => handleSectionClick("testing")}
                className="flex items-center gap-2"
              >
                <TestTube className="h-4 w-4" />
                Testing
              </Button>
            </div>
          )}
        </div>

        {/* Center - Broker URL display and edit */}
        {isLoggedIn && (
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <Server className="h-4 w-4" />
              <span className="hidden sm:inline-block">Broker:</span>
              <span className="font-mono text-xs sm:text-sm bg-muted px-2 py-0.5 rounded truncate max-w-[120px] sm:max-w-[200px]">
                {new URL(brokerUrl).host}
              </span>
            </div>
            <BrokerUrlDialog>
              <Button variant="outline" size="sm" className="h-8 w-8 p-0">
                <Settings2 className="h-4 w-4" />
              </Button>
            </BrokerUrlDialog>
          </div>
        )}

        {/* Center - Dynamic section buttons (only when not showing broker URL) */}
        {!isLoggedIn && activeSection && (
          <div className="flex items-center gap-2">
            {sectionRoutes.map((route) => (
              <Button
                key={route.href}
                variant={currentRoute === route.href ? "default" : "outline"}
                size="sm"
                onClick={() => handleRouteClick(route.href)}
              >
                {route.label}
              </Button>
            ))}
          </div>
        )}

        {/* Right side - Auth buttons */}
        <div className="flex items-center gap-4">
          {isLoggedIn ? (
            <>
              <span className="text-sm text-muted-foreground">Welcome, {user?.alias}</span>
              <Button variant="outline" size="sm" onClick={logout}>
                Logout
              </Button>
            </>
          ) : (
            <div className="flex items-center gap-2">
              <CreateUserDialog>
                <Button variant="outline" size="sm">
                  Create User
                </Button>
              </CreateUserDialog>
              <LoginDialog>
                <Button variant="outline" size="sm">
                  Login
                </Button>
              </LoginDialog>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
