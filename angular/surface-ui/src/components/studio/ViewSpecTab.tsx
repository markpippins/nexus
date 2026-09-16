import * as React from "react";
import { Link } from "@tanstack/react-router";
import { CapabilityId } from "@/core/types/designIR";
import { Region } from "@/core/types/viewSpec";
import { CAPABILITY_REGISTRY } from "@/lib/capabilities-registry";
import type { Widget } from "@/lib/widget-types";
import { Layers, LayoutGrid, ExternalLink, Sliders, Sparkles, Zap, ArrowRight } from "lucide-react";

interface ViewSpecTabProps {
  widget: Widget;
  selectedCapability: CapabilityId;
}

export function ViewSpecTab({ widget, selectedCapability }: ViewSpecTabProps) {
  const [selectedRegion, setSelectedRegion] = React.useState<Region>("main");
  const [density, setDensity] = React.useState<"compact" | "normal" | "spacious">("normal");
  const [priority, setPriority] = React.useState<"primary" | "secondary" | "supplementary">(
    "primary",
  );
  const [flex, setFlex] = React.useState("1");

  const capMeta = CAPABILITY_REGISTRY[selectedCapability] || CAPABILITY_REGISTRY.EntityCollection;

  // ViewSpec component node JSON representation
  const viewSpecNode = {
    id: `node-${widget.id}`,
    role: selectedRegion,
    contract: selectedCapability,
    widgetId: widget.id,
    componentName: widget.componentName,
    layout: {
      region: selectedRegion,
      density,
      priority,
      flex: Number(flex) || 1,
    },
    interactions: capMeta.outputEvents.map((evt) => ({
      verb: evt.name,
      targetAction: `bus.dispatch('${evt.name}', payload)`,
    })),
  };

  return (
    <div className="space-y-6">
      {/* Header Info Banner */}
      <div className="panel p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-foreground flex items-center gap-2">
              <Layers className="h-4 w-4 text-primary" />
              <span>ViewSpec Placement & Event Routing Graph</span>
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Defines how this widget node is compiled into a spatial ViewSpec layout tree and
              linked to sibling widgets via the reactive event bus.
            </p>
          </div>
          <Link
            to="/viewspec"
            className="flex items-center gap-1.5 rounded-md bg-accent/15 border border-accent/40 px-3 py-1.5 text-xs font-mono text-accent hover:bg-accent/25 transition-colors shadow-xs"
          >
            <span>Open ViewSpec Surface Studio</span>
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>
        </div>
      </div>

      {/* Configuration Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Spatial Layout Parameters */}
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <h4 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Sliders className="h-4 w-4 text-primary" />
              <span>Spatial Placement Specs</span>
            </h4>
            <span className="font-mono text-[10px] text-muted-foreground">Layout Node</span>
          </div>

          <div className="space-y-3.5">
            {/* Target Region */}
            <div className="space-y-1.5">
              <label className="label-mono text-[11px] text-muted-foreground">
                Target Surface Region:
              </label>
              <div className="grid grid-cols-4 gap-2">
                {(["header", "main", "sidebar", "footer"] as Region[]).map((r) => (
                  <button
                    key={r}
                    type="button"
                    onClick={() => setSelectedRegion(r)}
                    className={`py-2 px-3 rounded-md border font-mono text-xs capitalize transition-all ${
                      selectedRegion === r
                        ? "border-primary bg-primary/15 text-primary font-bold shadow-xs"
                        : "border-border/60 bg-surface/60 text-muted-foreground hover:bg-surface hover:text-foreground"
                    }`}
                  >
                    {r}
                  </button>
                ))}
              </div>
            </div>

            {/* Density */}
            <div className="space-y-1.5">
              <label className="label-mono text-[11px] text-muted-foreground">
                Visual Density:
              </label>
              <div className="grid grid-cols-3 gap-2">
                {(["compact", "normal", "spacious"] as const).map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => setDensity(d)}
                    className={`py-2 px-3 rounded-md border font-mono text-xs capitalize transition-all ${
                      density === d
                        ? "border-accent bg-accent/15 text-accent font-bold shadow-xs"
                        : "border-border/60 bg-surface/60 text-muted-foreground hover:bg-surface hover:text-foreground"
                    }`}
                  >
                    {d}
                  </button>
                ))}
              </div>
            </div>

            {/* Priority */}
            <div className="space-y-1.5">
              <label className="label-mono text-[11px] text-muted-foreground">
                Cognitive Priority:
              </label>
              <div className="grid grid-cols-3 gap-2">
                {(["primary", "secondary", "supplementary"] as const).map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPriority(p)}
                    className={`py-2 px-3 rounded-md border font-mono text-xs capitalize transition-all ${
                      priority === p
                        ? "border-signal bg-signal/15 text-signal font-bold shadow-xs"
                        : "border-border/60 bg-surface/60 text-muted-foreground hover:bg-surface hover:text-foreground"
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>

            {/* Flex Ratio */}
            <div className="space-y-1.5">
              <div className="flex justify-between">
                <label className="label-mono text-[11px] text-muted-foreground">
                  Spatial Flex Ratio:
                </label>
                <span className="font-mono text-xs text-foreground font-bold">{flex}x</span>
              </div>
              <input
                type="range"
                min="1"
                max="4"
                step="1"
                value={flex}
                onChange={(e) => setFlex(e.target.value)}
                className="w-full accent-primary"
              />
            </div>
          </div>
        </div>

        {/* Visual Surface Diagram */}
        <div className="panel p-5 space-y-4 flex flex-col justify-between">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <h4 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <LayoutGrid className="h-4 w-4 text-accent" />
              <span>Spatial Surface Layout Preview</span>
            </h4>
            <span className="font-mono text-[10px] text-accent">Active Placement</span>
          </div>

          {/* Diagram */}
          <div className="flex-1 min-h-52 rounded-lg border border-border/80 bg-background/80 p-3 flex flex-col gap-2 font-mono text-xs">
            {/* Header Area */}
            <div
              className={`rounded border p-2 text-center transition-all ${
                selectedRegion === "header"
                  ? "border-primary bg-primary/20 text-primary font-bold ring-1 ring-primary/50"
                  : "border-border/50 bg-surface/40 text-muted-foreground"
              }`}
            >
              HEADER REGION {selectedRegion === "header" && `[${widget.componentName}]`}
            </div>

            {/* Middle Row (Sidebar + Main) */}
            <div className="flex-1 grid grid-cols-12 gap-2 min-h-28">
              {/* Sidebar */}
              <div
                className={`col-span-4 rounded border p-2 flex items-center justify-center text-center transition-all ${
                  selectedRegion === "sidebar"
                    ? "border-primary bg-primary/20 text-primary font-bold ring-1 ring-primary/50"
                    : "border-border/50 bg-surface/40 text-muted-foreground"
                }`}
              >
                SIDEBAR {selectedRegion === "sidebar" && `[${widget.componentName}]`}
              </div>

              {/* Main Area */}
              <div
                className={`col-span-8 rounded border p-2 flex items-center justify-center text-center transition-all ${
                  selectedRegion === "main"
                    ? "border-primary bg-primary/20 text-primary font-bold ring-1 ring-primary/50"
                    : "border-border/50 bg-surface/40 text-muted-foreground"
                }`}
              >
                MAIN CONTENT REGION {selectedRegion === "main" && `[${widget.componentName}]`}
              </div>
            </div>

            {/* Footer Area */}
            <div
              className={`rounded border p-2 text-center transition-all ${
                selectedRegion === "footer"
                  ? "border-primary bg-primary/20 text-primary font-bold ring-1 ring-primary/50"
                  : "border-border/50 bg-surface/40 text-muted-foreground"
              }`}
            >
              FOOTER REGION {selectedRegion === "footer" && `[${widget.componentName}]`}
            </div>
          </div>

          <div className="text-[11px] text-muted-foreground flex items-center gap-2">
            <span className="live-dot" />
            <span>
              Compiled node will receive flex-grow: {flex} in region {selectedRegion.toUpperCase()}.
            </span>
          </div>
        </div>
      </div>

      {/* Compiled ViewSpec Node JSON */}
      <div className="panel p-5 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-accent" />
            <span>Compiled ViewSpec Layout Node JSON</span>
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            Node ID: {viewSpecNode.id}
          </span>
        </div>
        <pre className="max-h-64 overflow-auto rounded-lg border border-border/60 bg-background/80 p-3.5 font-mono text-xs text-foreground/90 leading-relaxed custom-scrollbar">
          {JSON.stringify(viewSpecNode, null, 2)}
        </pre>
      </div>
    </div>
  );
}
