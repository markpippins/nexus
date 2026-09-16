import * as React from "react";
import { Zap, Moon, Sun, Layers, Check, ChevronDown } from "lucide-react";
import { useTheme, ThemeMode, THEME_OPTIONS } from "@/hooks/use-theme";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface ThemeToggleProps {
  variant?: "header" | "sidebar" | "compact";
  className?: string;
}

function getThemeIcon(mode: ThemeMode, className = "h-3.5 w-3.5") {
  switch (mode) {
    case "light":
      return <Sun className={`${className} text-amber-500`} />;
    case "steel":
      return <Layers className={`${className} text-slate-400`} />;
    case "circuit":
      return <Zap className={`${className} text-primary animate-pulse`} />;
    case "dark":
    default:
      return <Moon className={`${className} text-primary/80`} />;
  }
}

export function ThemeToggle({ variant = "header", className = "" }: ThemeToggleProps) {
  const { theme, setTheme, cycleTheme, themes } = useTheme();
  const currentTheme = themes.find((t) => t.id === theme) || themes[0];

  if (variant === "compact") {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={`theme-transition relative flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface text-muted-foreground hover:border-primary/50 hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary ${className}`}
            title={`Active theme: ${currentTheme.name}. Click to change theme.`}
            aria-label={`Select theme (currently ${currentTheme.name})`}
          >
            {getThemeIcon(theme, "h-4 w-4")}
            <span
              className={`absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full border border-background ${
                theme === "circuit"
                  ? "bg-primary shadow-[0_0_8px_var(--color-primary)]"
                  : theme === "light"
                    ? "bg-amber-500"
                    : theme === "steel"
                      ? "bg-slate-400"
                      : "bg-signal"
              }`}
            />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56 font-mono text-xs p-1">
          <DropdownMenuLabel className="text-[10px] text-muted-foreground px-2 py-1 uppercase tracking-wider">
            Workbench Theme
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {THEME_OPTIONS.map((opt) => (
            <DropdownMenuItem
              key={opt.id}
              onClick={() => setTheme(opt.id)}
              className="flex items-center justify-between cursor-pointer py-1.5 px-2 text-xs"
            >
              <div className="flex items-center gap-2">
                {getThemeIcon(opt.id, "h-3.5 w-3.5")}
                <span
                  className={
                    theme === opt.id ? "font-bold text-foreground" : "text-muted-foreground"
                  }
                >
                  {opt.name}
                </span>
              </div>
              {theme === opt.id && <Check className="h-3.5 w-3.5 text-primary" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  if (variant === "sidebar") {
    return (
      <div className={`space-y-1.5 ${className}`}>
        <div className="flex items-center justify-between px-1 text-[10px] font-mono text-muted-foreground uppercase tracking-wider">
          <span>Theme Engine</span>
          <span className="text-primary font-bold">{currentTheme.shortName}</span>
        </div>
        <div className="grid grid-cols-2 gap-1 font-mono text-xs">
          {THEME_OPTIONS.map((opt) => {
            const isSelected = theme === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => setTheme(opt.id)}
                className={`theme-transition flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[11px] transition-all text-left ${
                  isSelected
                    ? "border-primary bg-primary/15 text-primary font-bold shadow-xs ring-1 ring-primary/40"
                    : "border-sidebar-border bg-sidebar text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
                }`}
                title={opt.description}
              >
                {getThemeIcon(opt.id, "h-3 w-3 shrink-0")}
                <span className="truncate">{opt.shortName}</span>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // Header default variant: rich dropdown selector with quick visual preview
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={`theme-transition border-border bg-surface text-xs font-mono hover:border-primary/50 hover:bg-muted gap-1.5 ${className}`}
          title={`Active Theme: ${currentTheme.name}. Click to change theme.`}
          aria-label={`Active Theme: ${currentTheme.name}`}
        >
          {getThemeIcon(theme, "h-3.5 w-3.5")}
          <span className="hidden sm:inline">{currentTheme.name}</span>
          <ChevronDown className="h-3 w-3 text-muted-foreground opacity-70" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-64 font-mono text-xs p-1.5 bg-surface border-border"
      >
        <DropdownMenuLabel className="text-[10px] text-muted-foreground px-2 py-1 uppercase tracking-wider flex items-center justify-between">
          <span>Workbench Theme</span>
          <span className="text-[9px] text-primary">4 Presets</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-border/60" />
        <div className="space-y-0.5 py-1">
          {THEME_OPTIONS.map((opt) => {
            const isSelected = theme === opt.id;
            return (
              <DropdownMenuItem
                key={opt.id}
                onClick={() => setTheme(opt.id)}
                className={`flex items-start justify-between cursor-pointer rounded-md p-2 transition-colors ${
                  isSelected
                    ? "bg-primary/15 text-primary font-semibold border border-primary/30"
                    : "hover:bg-muted text-foreground"
                }`}
              >
                <div className="flex items-start gap-2.5">
                  <div className="mt-0.5">{getThemeIcon(opt.id, "h-4 w-4")}</div>
                  <div className="space-y-0.5">
                    <div className="font-semibold text-xs leading-none">{opt.name}</div>
                    <div className="text-[10px] text-muted-foreground font-sans line-clamp-1">
                      {opt.description}
                    </div>
                  </div>
                </div>
                {isSelected && <Check className="h-4 w-4 text-primary shrink-0 ml-1 mt-0.5" />}
              </DropdownMenuItem>
            );
          })}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
