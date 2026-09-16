import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { Toaster } from "sonner";

import appCss from "../styles.css?url";
import { reportLovableError } from "../lib/lovable-error-reporting";
import { WorkbenchSidebar } from "@/components/WorkbenchSidebar";
import { SiteHeader } from "@/components/SiteHeader";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          This page didn't load
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Something went wrong on our end. You can try refreshing or head back home.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Reactive Relics — Interactive Widget Workbench" },
      {
        name: "description",
        content:
          "A dark technical workbench for composing, sandboxing, and analyzing reactive UI widgets.",
      },
      { name: "theme-color", content: "#0f141c" },
      { property: "og:title", content: "Reactive Relics — Interactive Widget Workbench" },
      {
        property: "og:description",
        content:
          "A dark technical workbench for composing, sandboxing, and analyzing reactive UI widgets.",
      },
      { property: "og:type", content: "website" },
    ],
    links: [
      {
        rel: "stylesheet",
        href: appCss,
      },
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon" },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      {
        rel: "preconnect",
        href: "https://fonts.gstatic.com",
        crossOrigin: "anonymous",
      },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap",
      },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("relics_workbench_theme");var all=["dark","light","steel","circuit"];if(t&&all.indexOf(t)>-1){all.forEach(function(c){document.documentElement.classList.remove(c);});document.documentElement.classList.add(t);}else{document.documentElement.classList.add("dark");}var f=localStorage.getItem("relics_workbench_font");if(f==="system-ui"){document.documentElement.classList.add("font-system");document.documentElement.setAttribute("data-font","system-ui");}else{document.documentElement.classList.add("font-space-grotesk");document.documentElement.setAttribute("data-font","space-grotesk");}}catch(e){}})();`,
          }}
        />
        <HeadContent />
      </head>
      <body
        className="min-h-screen bg-background text-foreground antialiased selection:bg-primary/20 selection:text-primary"
        suppressHydrationWarning
      >
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
        {/* Desktop Left Sidebar Navigation */}
        <div className="hidden md:flex md:shrink-0">
          <WorkbenchSidebar />
        </div>

        {/* Mobile Sidebar Overlay Drawer */}
        {mobileNavOpen && (
          <div className="fixed inset-0 z-50 flex md:hidden" role="dialog" aria-modal="true">
            <div
              className="fixed inset-0 bg-background/80 backdrop-blur-sm transition-opacity"
              onClick={() => setMobileNavOpen(false)}
            />
            <div className="relative z-10 flex w-72 flex-1 shadow-2xl">
              <WorkbenchSidebar onCloseMobile={() => setMobileNavOpen(false)} />
            </div>
          </div>
        )}

        {/* Main Workbench Viewport */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <SiteHeader onToggleSidebar={() => setMobileNavOpen((prev) => !prev)} />

          <main className="flex-1 overflow-y-auto" id="workbench-main-content">
            <Outlet />

            {/* Global Workbench Footer */}
            <footer className="border-t border-border bg-surface/50 py-6 text-xs text-muted-foreground">
              <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-4 sm:flex-row sm:px-6">
                <div className="flex items-center gap-2 font-mono">
                  <span className="live-dot" />
                  <span>REACTIVE RELICS // WORKBENCH ENGINE v0.9</span>
                </div>
                <div className="flex flex-wrap items-center justify-center gap-4 font-mono text-[11px]">
                  <span>AST Prop Inference</span>
                  <span>•</span>
                  <span>Mock API Streams</span>
                  <span>•</span>
                  <span>In-Memory Sandbox</span>
                </div>
              </div>
            </footer>
          </main>
        </div>
      </div>

      <Toaster
        theme="dark"
        toastOptions={{
          className: "bg-surface border-border text-foreground font-mono text-xs shadow-panel",
        }}
      />
    </QueryClientProvider>
  );
}
