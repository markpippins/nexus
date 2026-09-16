import { Link, useLocation } from "@tanstack/react-router";
import {
  Activity,
  Code2,
  Cpu,
  Database,
  Gauge,
  Layers,
  Plus,
  RefreshCw,
  Sliders,
  Sparkles,
  Terminal,
  Wrench,
  X,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { useCatalog } from "@/lib/storage";
import type { WidgetType } from "@/lib/widget-types";
import { ThemeToggle } from "./ThemeToggle";
import { FontToggle } from "./FontToggle";

interface WorkbenchSidebarProps {
  onCloseMobile?: () => void;
}

export function WorkbenchSidebar({ onCloseMobile }: WorkbenchSidebarProps) {
  const location = useLocation();
  const { widgets, reset } = useCatalog();

  const handleReset = () => {
    reset();
    toast.success("Restored default seed relics.");
  };

  const isCurrentPath = (path: string) => {
    if (path === "/" && location.pathname === "/") return true;
    if (path !== "/" && location.pathname.startsWith(path)) return true;
    return false;
  };

  // Group counts by type
  const typeCounts = React.useMemo(() => {
    const counts: Record<WidgetType, number> = {
      "react-component": 0,
      "data-vis": 0,
      "interactive-tool": 0,
      "control-surface": 0,
      "canvas-element": 0,
    };
    for (const w of widgets) {
      const t = w.type || "react-component";
      counts[t] = (counts[t] || 0) + 1;
    }
    return counts;
  }, [widgets]);

  // Unique detected APIs count
  const uniqueApisCount = React.useMemo(() => {
    const set = new Set<string>();
    for (const w of widgets) {
      for (const e of w.endpoints) {
        set.add(e.signature);
      }
    }
    return set.size;
  }, [widgets]);

  return (
    <aside
      className="flex h-full w-64 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground select-none"
      id="workbench-sidebar"
    >
      {/* Brand header */}
      <div className="flex h-14 items-center justify-between border-b border-sidebar-border px-4">
        <Link
          to="/"
          onClick={onCloseMobile}
          className="flex items-center gap-2 font-mono text-sm font-semibold tracking-tight text-sidebar-foreground transition-opacity hover:opacity-90"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground shadow-sm">
            <Cpu className="h-4 w-4" />
          </div>
          <div className="flex flex-col">
            <span className="leading-tight font-bold text-foreground">reactive-relics</span>
            <span className="font-mono text-[9px] uppercase tracking-widest text-muted-foreground">
              workbench v0.9
            </span>
          </div>
        </Link>

        {onCloseMobile && (
          <button
            onClick={onCloseMobile}
            className="rounded-md p-1.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground md:hidden"
            aria-label="Close sidebar"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Engine Status Bar */}
      <div className="border-b border-sidebar-border/70 bg-surface/40 px-3.5 py-2">
        <div className="flex items-center justify-between text-[11px] font-mono">
          <div className="flex items-center gap-2">
            <span className="live-dot" />
            <span className="text-muted-foreground">ENGINE:</span>
            <span className="font-semibold text-accent">ONLINE</span>
          </div>
          <span className="rounded border border-sidebar-border bg-sidebar-accent/50 px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {widgets.length} relics
          </span>
        </div>
      </div>

      {/* Scrollable Navigation Area */}
      <div className="flex-1 space-y-5 overflow-y-auto p-3 custom-scrollbar">
        {/* Main workbench routes */}
        <div>
          <div className="px-2 pb-1.5 label-mono text-muted-foreground/80 text-[10px]">
            Navigation
          </div>
          <div className="space-y-1">
            <Link
              to="/"
              onClick={onCloseMobile}
              className={`flex items-center justify-between rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                isCurrentPath("/") &&
                !location.pathname.startsWith("/new") &&
                !location.pathname.startsWith("/widget")
                  ? "bg-sidebar-primary text-sidebar-primary-foreground font-semibold shadow-sm"
                  : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              }`}
            >
              <div className="flex items-center gap-2.5">
                <Layers className="h-4 w-4 shrink-0" />
                <span>Relic Catalog</span>
              </div>
              <span
                className={`rounded px-1.5 py-0.2 font-mono text-[10px] ${
                  isCurrentPath("/") &&
                  !location.pathname.startsWith("/new") &&
                  !location.pathname.startsWith("/widget")
                    ? "bg-sidebar-primary-foreground/20 text-sidebar-primary-foreground"
                    : "bg-sidebar-accent text-muted-foreground"
                }`}
              >
                {widgets.length}
              </span>
            </Link>

            <Link
              to="/new"
              onClick={onCloseMobile}
              className={`flex items-center justify-between rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                isCurrentPath("/new")
                  ? "bg-sidebar-primary text-sidebar-primary-foreground font-semibold shadow-sm"
                  : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              }`}
            >
              <div className="flex items-center gap-2.5">
                <Plus className="h-4 w-4 shrink-0" />
                <span>Mount New Relic</span>
              </div>
              <span
                className={`rounded px-1.5 py-0.2 font-mono text-[10px] ${
                  isCurrentPath("/new")
                    ? "bg-sidebar-primary-foreground/20 text-sidebar-primary-foreground"
                    : "bg-sidebar-accent text-accent"
                }`}
              >
                AST
              </span>
            </Link>

            <Link
              to="/viewspec"
              onClick={onCloseMobile}
              className={`flex items-center justify-between rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                isCurrentPath("/viewspec")
                  ? "bg-sidebar-primary text-sidebar-primary-foreground font-semibold shadow-sm"
                  : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              }`}
            >
              <div className="flex items-center gap-2.5">
                <Sparkles className="h-4 w-4 shrink-0 text-accent" />
                <span>ViewSpec Surface</span>
              </div>
              <span
                className={`rounded px-1.5 py-0.2 font-mono text-[10px] ${
                  isCurrentPath("/viewspec")
                    ? "bg-sidebar-primary-foreground/20 text-sidebar-primary-foreground"
                    : "bg-sidebar-accent text-primary font-bold"
                }`}
              >
                IR
              </span>
            </Link>
          </div>
        </div>

        {/* Relic Types / Archetypes */}
        <div>
          <div className="px-2 pb-1.5 label-mono text-muted-foreground/80 text-[10px]">
            Archetypes
          </div>
          <div className="space-y-0.5 font-mono text-xs">
            <Link
              to="/"
              search={{ type: "react-component" }}
              onClick={onCloseMobile}
              className="flex items-center justify-between rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group"
            >
              <div className="flex items-center gap-2">
                <Code2 className="h-3.5 w-3.5 text-accent group-hover:scale-110 transition-transform" />
                <span className="font-sans text-xs">React Component</span>
              </div>
              <span className="text-[11px] opacity-70">{typeCounts["react-component"]}</span>
            </Link>

            <Link
              to="/"
              search={{ type: "data-vis" }}
              onClick={onCloseMobile}
              className="flex items-center justify-between rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group"
            >
              <div className="flex items-center gap-2">
                <Activity className="h-3.5 w-3.5 text-primary group-hover:scale-110 transition-transform" />
                <span className="font-sans text-xs">Data Visualization</span>
              </div>
              <span className="text-[11px] opacity-70">{typeCounts["data-vis"]}</span>
            </Link>

            <Link
              to="/"
              search={{ type: "interactive-tool" }}
              onClick={onCloseMobile}
              className="flex items-center justify-between rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group"
            >
              <div className="flex items-center gap-2">
                <Wrench className="h-3.5 w-3.5 text-emerald-400 group-hover:scale-110 transition-transform" />
                <span className="font-sans text-xs">Interactive Tool</span>
              </div>
              <span className="text-[11px] opacity-70">{typeCounts["interactive-tool"]}</span>
            </Link>

            <Link
              to="/"
              search={{ type: "control-surface" }}
              onClick={onCloseMobile}
              className="flex items-center justify-between rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group"
            >
              <div className="flex items-center gap-2">
                <Gauge className="h-3.5 w-3.5 text-amber-400 group-hover:scale-110 transition-transform" />
                <span className="font-sans text-xs">Control Surface</span>
              </div>
              <span className="text-[11px] opacity-70">{typeCounts["control-surface"]}</span>
            </Link>

            <Link
              to="/"
              search={{ type: "canvas-element" }}
              onClick={onCloseMobile}
              className="flex items-center justify-between rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group"
            >
              <div className="flex items-center gap-2">
                <Sparkles className="h-3.5 w-3.5 text-purple-400 group-hover:scale-110 transition-transform" />
                <span className="font-sans text-xs">Canvas Element</span>
              </div>
              <span className="text-[11px] opacity-70">{typeCounts["canvas-element"]}</span>
            </Link>
          </div>
        </div>

        {/* Pinned / Active Relics */}
        <div>
          <div className="flex items-center justify-between px-2 pb-1.5">
            <span className="label-mono text-muted-foreground/80 text-[10px]">Mounted Relics</span>
            <span className="font-mono text-[10px] text-muted-foreground">{widgets.length}</span>
          </div>
          <div className="space-y-0.5">
            {widgets.map((w) => {
              const isActive = location.pathname === `/widget/${w.id}`;
              return (
                <Link
                  key={w.id}
                  to="/widget/$id"
                  params={{ id: w.id }}
                  onClick={onCloseMobile}
                  className={`flex items-center justify-between rounded-md px-2.5 py-1.5 text-xs transition-colors ${
                    isActive
                      ? "border border-sidebar-primary/40 bg-sidebar-accent font-semibold text-sidebar-primary shadow-xs"
                      : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
                  }`}
                >
                  <div className="flex items-center gap-2 truncate">
                    <span
                      className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                        isActive ? "bg-sidebar-primary" : "bg-muted-foreground/40"
                      }`}
                    />
                    <span className="truncate">{w.name}</span>
                  </div>
                  <span className="font-mono text-[10px] text-muted-foreground shrink-0">
                    {w.endpoints.length > 0 ? "REST" : "LOCAL"}
                  </span>
                </Link>
              );
            })}
          </div>
        </div>
      </div>

      {/* Footer Diagnostic & Mode Bar */}
      <div className="border-t border-sidebar-border bg-surface/50 p-3 space-y-2.5 text-xs">
        <ThemeToggle variant="sidebar" />
        <FontToggle variant="sidebar" />

        <div className="space-y-1 font-mono text-[10px] text-muted-foreground">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1">
              <Database className="h-3 w-3" />
              <span>Storage</span>
            </span>
            <span className="text-foreground">Local + InMemory</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1">
              <Terminal className="h-3 w-3" />
              <span>Endpoints</span>
            </span>
            <span className="text-accent">{uniqueApisCount} detected</span>
          </div>
        </div>

        <button
          onClick={handleReset}
          type="button"
          className="flex w-full items-center justify-center gap-1.5 rounded-md border border-sidebar-border bg-sidebar py-1.5 font-mono text-[11px] text-muted-foreground transition-colors hover:border-sidebar-primary/40 hover:bg-sidebar-accent hover:text-foreground"
        >
          <RefreshCw className="h-3 w-3" />
          <span>Restore Seed Catalog</span>
        </button>
      </div>
    </aside>
  );
}
