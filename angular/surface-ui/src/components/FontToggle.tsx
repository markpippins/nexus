import * as React from "react";
import { Type, Check, ChevronDown, Sparkles, Eye } from "lucide-react";
import { useFont, FontMode, FONT_OPTIONS } from "@/hooks/use-font";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface FontToggleProps {
  variant?: "header" | "sidebar" | "compact";
  className?: string;
}

export function FontToggle({ variant = "header", className = "" }: FontToggleProps) {
  const { font, isSystemFont, setFont, toggleFont, fontOptions } = useFont();
  const currentOption = fontOptions.find((o) => o.id === font) || fontOptions[0];

  if (variant === "compact") {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={`theme-transition relative flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface text-muted-foreground hover:border-primary/50 hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary ${className}`}
            title={`Active font: ${currentOption.name}. Click to switch font.`}
            aria-label={`Select typography font (currently ${currentOption.name})`}
          >
            <Type className="h-4 w-4" />
            <span
              className={`absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full border border-background ${
                isSystemFont ? "bg-accent" : "bg-primary"
              }`}
            />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="w-60 font-mono text-xs p-1 bg-surface border-border"
        >
          <DropdownMenuLabel className="text-[10px] text-muted-foreground px-2 py-1 uppercase tracking-wider flex items-center justify-between">
            <span>Typography / Font</span>
            <span className="text-[9px] text-primary">Accessibility</span>
          </DropdownMenuLabel>
          <DropdownMenuSeparator className="bg-border/60" />
          {fontOptions.map((opt) => (
            <DropdownMenuItem
              key={opt.id}
              onClick={() => setFont(opt.id)}
              className="flex items-center justify-between cursor-pointer py-1.5 px-2 text-xs"
            >
              <div className="flex items-center gap-2">
                <Type className="h-3.5 w-3.5 text-primary" />
                <span
                  className={
                    font === opt.id ? "font-bold text-foreground" : "text-muted-foreground"
                  }
                >
                  {opt.name}
                </span>
              </div>
              {font === opt.id && <Check className="h-3.5 w-3.5 text-primary" />}
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
          <span className="flex items-center gap-1">
            <Type className="h-3 w-3 text-primary" />
            <span>Typography</span>
          </span>
          <span className="text-primary font-bold text-[9px]">{currentOption.shortName}</span>
        </div>
        <div className="grid grid-cols-2 gap-1 font-mono text-xs">
          {fontOptions.map((opt) => {
            const isSelected = font === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => setFont(opt.id)}
                className={`theme-transition flex items-center justify-center gap-1 rounded-md border px-2 py-1.5 text-[10px] transition-all ${
                  isSelected
                    ? "border-primary bg-primary/15 text-primary font-bold shadow-xs ring-1 ring-primary/40"
                    : "border-sidebar-border bg-sidebar text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
                }`}
                title={opt.description}
              >
                <span className="truncate">{opt.shortName}</span>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // Header default variant: dropdown selector with previews
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={`theme-transition border-border bg-surface text-xs font-mono hover:border-primary/50 hover:bg-muted gap-1.5 ${className}`}
          title={`Active Font: ${currentOption.name}. Click to change font.`}
          aria-label={`Active Font: ${currentOption.name}`}
        >
          <Type className="h-3.5 w-3.5 text-primary" />
          <span className="hidden md:inline">{currentOption.shortName}</span>
          <span className="md:hidden">Font</span>
          <ChevronDown className="h-3 w-3 text-muted-foreground opacity-70" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-72 font-mono text-xs p-1.5 bg-surface border-border shadow-lg"
      >
        <DropdownMenuLabel className="text-[10px] text-muted-foreground px-2 py-1 uppercase tracking-wider flex items-center justify-between">
          <span className="flex items-center gap-1.5">
            <Type className="h-3 w-3 text-primary" />
            <span>Typography Engine</span>
          </span>
          <span className="text-[9px] rounded bg-primary/15 text-primary px-1.5 py-0.2 font-semibold">
            Accessibility
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-border/60" />
        <div className="space-y-1 py-1">
          {fontOptions.map((opt) => {
            const isSelected = font === opt.id;
            return (
              <DropdownMenuItem
                key={opt.id}
                onClick={() => setFont(opt.id)}
                className={`flex items-start justify-between cursor-pointer rounded-md p-2 transition-colors ${
                  isSelected
                    ? "bg-primary/15 text-primary font-semibold border border-primary/30"
                    : "hover:bg-muted text-foreground"
                }`}
              >
                <div className="flex items-start gap-2.5">
                  <div className="mt-0.5 rounded p-1 bg-background/60 border border-border/60 text-primary">
                    <Type className="h-3.5 w-3.5" />
                  </div>
                  <div className="space-y-0.5">
                    <div className="font-semibold text-xs leading-none flex items-center gap-1.5">
                      <span>{opt.name}</span>
                      {opt.id === "system-ui" && (
                        <span className="rounded bg-accent/20 text-accent text-[9px] px-1 py-0.1">
                          A11y
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-muted-foreground font-sans line-clamp-2">
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
