import * as React from "react";
import {
  Activity,
  Code2,
  Cpu,
  Gauge,
  Layers,
  Radio,
  Sliders,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { Widget, WidgetType } from "@/lib/widget-types";

export interface RelicTypeDefinition {
  type: WidgetType;
  title: string;
  shortCode: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  accentColor: string;
  badgeClass: string;
  capabilities: string[];
}

export const RELIC_TYPE_DEFINITIONS: RelicTypeDefinition[] = [
  {
    type: "react-component",
    title: "React Component",
    shortCode: "COMP",
    description:
      "Self-contained reactive UI components with automatic AST prop inference and simulated DOM state.",
    icon: Code2,
    accentColor: "text-accent",
    badgeClass: "border-accent/40 bg-accent/10 text-accent",
    capabilities: ["Prop Auto-Inference", "Simulated State", "DOM Tree"],
  },
  {
    type: "data-vis",
    title: "Data Visualization",
    shortCode: "DVIS",
    description:
      "Time-series charts, sparklines, and metric monitors fed by continuous mock API streaming loops.",
    icon: Activity,
    accentColor: "text-primary",
    badgeClass: "border-primary/40 bg-primary/10 text-primary",
    capabilities: ["Mock Data Stream", "SVG & Canvas", "Interpolated Updates"],
  },
  {
    type: "interactive-tool",
    title: "Interactive Tool",
    shortCode: "TOOL",
    description: "Real-time calculators, schema converters, testing rigs, and developer utilities.",
    icon: Wrench,
    accentColor: "text-emerald-400",
    badgeClass: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
    capabilities: ["Two-Way Binding", "Event Interceptor", "State Isolation"],
  },
  {
    type: "control-surface",
    title: "Control Surface",
    shortCode: "CTRL",
    description: "Knobs, sliders, telemetry toggles, and high-frequency parameter dispatchers.",
    icon: Gauge,
    accentColor: "text-amber-400",
    badgeClass: "border-amber-500/40 bg-amber-500/10 text-amber-400",
    capabilities: ["Param Mapping", "Instant Dispatch", "Feedback Loop"],
  },
  {
    type: "canvas-element",
    title: "Canvas Element",
    shortCode: "CNVS",
    description:
      "Hardware-accelerated 2D graphics, particle engines, and generative algorithm relics.",
    icon: Sparkles,
    accentColor: "text-purple-400",
    badgeClass: "border-purple-500/40 bg-purple-500/10 text-purple-400",
    capabilities: ["Raf Loop", "Pixel Pipelines", "Interactive Physics"],
  },
];

interface RelicTypesGridProps {
  widgets: Widget[];
  selectedType: WidgetType | "all";
  onSelectType: (type: WidgetType | "all") => void;
}

export function RelicTypesGrid({ widgets, selectedType, onSelectType }: RelicTypesGridProps) {
  // Count widgets by type
  const counts = React.useMemo(() => {
    const map: Record<string, number> = {
      all: widgets.length,
      "react-component": 0,
      "data-vis": 0,
      "interactive-tool": 0,
      "control-surface": 0,
      "canvas-element": 0,
    };
    for (const w of widgets) {
      const t = w.type ?? "react-component";
      map[t] = (map[t] || 0) + 1;
    }
    return map;
  }, [widgets]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-accent" />
          <span className="label-mono text-accent">relic archetypes</span>
        </div>
        <button
          onClick={() => onSelectType("all")}
          className={`font-mono text-xs transition-colors hover:text-primary ${
            selectedType === "all"
              ? "text-primary font-semibold underline decoration-primary/40 underline-offset-4"
              : "text-muted-foreground"
          }`}
        >
          View All ({widgets.length})
        </button>
      </div>

      {/* Grid of relic types with shadow-glow on hover */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {RELIC_TYPE_DEFINITIONS.map((def) => {
          const Icon = def.icon;
          const count = counts[def.type] ?? 0;
          const isSelected = selectedType === def.type;

          return (
            <button
              key={def.type}
              type="button"
              onClick={() => onSelectType(isSelected ? "all" : def.type)}
              className={`group relative flex flex-col justify-between rounded-lg border p-4 text-left transition-all duration-200 cursor-pointer ${
                isSelected
                  ? "border-primary bg-surface shadow-glow"
                  : "border-border bg-surface/70 hover:border-primary/60 hover:bg-surface hover:shadow-glow"
              }`}
            >
              <div>
                <div className="flex items-center justify-between">
                  <div
                    className={`flex h-8 w-8 items-center justify-center rounded-md border ${def.badgeClass}`}
                  >
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="flex items-center gap-1.5 font-mono text-[10px]">
                    <span className="text-muted-foreground">COUNT</span>
                    <span className="rounded bg-muted px-1.5 py-0.5 font-semibold text-foreground">
                      {count}
                    </span>
                  </div>
                </div>

                <div className="mt-3">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-[10px] text-muted-foreground">
                      [{def.shortCode}]
                    </span>
                    <h4 className="font-semibold text-sm text-foreground group-hover:text-primary transition-colors">
                      {def.title}
                    </h4>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                    {def.description}
                  </p>
                </div>
              </div>

              <div className="mt-3 border-t border-border/60 pt-2">
                <div className="flex flex-wrap gap-1">
                  {def.capabilities.map((cap) => (
                    <span
                      key={cap}
                      className="rounded border border-border/40 bg-background/50 px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground"
                    >
                      {cap}
                    </span>
                  ))}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
