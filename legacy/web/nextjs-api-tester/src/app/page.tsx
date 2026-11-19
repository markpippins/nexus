"use client";

import { useAuth } from "@/contexts/auth-context";
import { useNavigation } from "@/contexts/navigation-context";

export default function Home() {
  const { isLoggedIn } = useAuth();
  const { activeSection } = useNavigation();

  // Don't clear activeSection - let the toolbar manage it

  if (!isLoggedIn) {
    return (
       <main className="container mx-auto p-4 sm:p-8">
        <div className="flex flex-col items-center justify-center space-y-2 mb-12">
          <h1 className="text-4xl font-bold tracking-tight">Welcome to API Tester</h1>
          <p className="text-muted-foreground text-center">
            Please log in to continue.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="container mx-auto p-4 sm:p-8">
      <div className="flex flex-col items-center justify-center space-y-2 mb-12">
        <h1 className="text-4xl font-bold tracking-tight">API Tester</h1>
        <p className="text-muted-foreground text-center">
          {activeSection 
            ? `${activeSection.charAt(0).toUpperCase() + activeSection.slice(1)} section selected. Choose a tool from the toolbar above.`
            : "Select a section from the toolbar above to access different tools and features."
          }
        </p>
      </div>
    </main>
  );
}
