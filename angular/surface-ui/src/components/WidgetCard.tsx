import { Link } from "@tanstack/react-router";
import { WidgetSandbox } from "./WidgetSandbox";
import { defaultProps } from "@/lib/widget-props";
import type { Widget } from "@/lib/widget-types";
import { useHydrated } from "@/hooks/use-hydrated";

export function WidgetCard({ widget }: { widget: Widget }) {
  const hydrated = useHydrated();
  const endpoint = widget.endpoints[0];

  const typeLabels: Record<string, { label: string; color: string }> = {
    "react-component": { label: "Component", color: "text-accent border-accent/30 bg-accent/10" },
    "data-vis": { label: "Data Vis", color: "text-primary border-primary/30 bg-primary/10" },
    "interactive-tool": {
      label: "Tool",
      color: "text-emerald-400 border-emerald-500/30 bg-emerald-500/10",
    },
    "control-surface": {
      label: "Control",
      color: "text-amber-400 border-amber-500/30 bg-amber-500/10",
    },
    "canvas-element": {
      label: "Canvas",
      color: "text-purple-400 border-purple-500/30 bg-purple-500/10",
    },
  };

  const typeInfo = widget.type ? typeLabels[widget.type] : undefined;

  return (
    <Link
      to="/widget/$id"
      params={{ id: widget.id }}
      className="panel group flex flex-col overflow-hidden transition-all duration-200 hover:border-primary/60 hover:shadow-glow"
    >
      <div className="flex items-center justify-between border-b border-border bg-surface/80 px-4 py-2">
        <span className="label-mono truncate">
          {endpoint ? endpoint.signature : "no api detected"}
        </span>
        <div className="flex items-center gap-2">
          {typeInfo && (
            <span
              className={`rounded border px-1.5 py-0.2 font-mono text-[10px] uppercase font-medium ${typeInfo.color}`}
            >
              {typeInfo.label}
            </span>
          )}
          <span className="live-dot shrink-0" aria-hidden />
        </div>
      </div>
      <div className="flex min-h-44 items-center justify-center bg-background/40 p-5">
        {hydrated ? (
          <WidgetSandbox widget={widget} props={defaultProps(widget)} className="w-full" />
        ) : (
          <span className="label-mono">booting sandbox…</span>
        )}
      </div>
      <div className="border-t border-border p-4">
        <h3 className="font-semibold text-foreground group-hover:text-primary transition-colors">
          {widget.name}
        </h3>
        <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{widget.description}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {widget.inputs.map((i) => (
            <span
              key={i.name}
              className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
            >
              {i.name}
            </span>
          ))}
          {widget.tags.map((t) => (
            <span
              key={t}
              className="rounded border border-border/50 bg-background/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground/80"
            >
              #{t}
            </span>
          ))}
        </div>
      </div>
    </Link>
  );
}
