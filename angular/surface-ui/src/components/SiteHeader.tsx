import { Link } from "@tanstack/react-router";
import { Cpu, Menu, Plus } from "lucide-react";
import { Button } from "./ui/button";
import { ThemeToggle } from "./ThemeToggle";
import { FontToggle } from "./FontToggle";

interface SiteHeaderProps {
  onToggleSidebar?: () => void;
}

export function SiteHeader({ onToggleSidebar }: SiteHeaderProps) {
  return (
    <header className="sticky top-0 z-30 flex h-14 w-full items-center justify-between border-b border-border bg-background/85 px-4 backdrop-blur-md sm:px-6">
      <div className="flex items-center gap-3">
        {onToggleSidebar && (
          <button
            onClick={onToggleSidebar}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
            aria-label="Toggle workbench sidebar"
          >
            <Menu className="h-4 w-4" />
          </button>
        )}

        <Link to="/" className="flex items-center gap-2 font-mono text-sm font-semibold">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Cpu className="h-4 w-4" />
          </div>
          <span className="tracking-tight text-foreground hidden sm:inline">reactive-relics</span>
          <span className="label-mono rounded border border-border px-1.5 py-0.5 text-[10px]">
            workbench
          </span>
        </Link>
      </div>

      <div className="flex items-center gap-2 sm:gap-2.5">
        <div className="hidden items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1 text-xs text-muted-foreground lg:flex">
          <span className="live-dot" />
          <span className="font-mono text-[11px]">engine online</span>
        </div>

        <ThemeToggle />
        <FontToggle />

        <Button
          asChild
          variant="outline"
          size="sm"
          className="border-border bg-surface text-xs hover:bg-muted font-mono hidden sm:inline-flex"
        >
          <Link to="/viewspec">ViewSpec</Link>
        </Button>

        <Button
          asChild
          variant="outline"
          size="sm"
          className="border-border bg-surface text-xs hover:bg-muted font-mono hidden sm:inline-flex"
        >
          <Link to="/">Catalog</Link>
        </Button>

        <Button
          asChild
          size="sm"
          className="bg-primary text-primary-foreground hover:bg-primary/90 text-xs font-mono font-medium"
        >
          <Link to="/new">
            <Plus className="mr-1 h-3.5 w-3.5" />
            <span className="hidden xs:inline">Mount</span> Relic
          </Link>
        </Button>
      </div>
    </header>
  );
}
