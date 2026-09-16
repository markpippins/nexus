import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Search, RotateCcw, Plus, Filter, Sparkles } from "lucide-react";
import { GovernanceDemo } from "@/components/runtime/GovernanceWorkbench";
import { WidgetCard } from "@/components/WidgetCard";
import { RelicTypesGrid } from "@/components/RelicTypesGrid";
import { useCatalog } from "@/lib/storage";
import type { WidgetType } from "@/lib/widget-types";

export const Route = createFileRoute("/")({
  validateSearch: (search: Record<string, unknown>): { type?: WidgetType } => {
    return {
      type: typeof search.type === "string" ? (search.type as WidgetType) : undefined,
    };
  },
  head: () => ({
    meta: [
      { title: "Reactive Relics — Workbench & Live Widget Catalog" },
      {
        name: "description",
        content:
          "Live interactive catalog of reactive UI widgets, AST-inferred schema analysis, and mocked API sandbox streams.",
      },
      { property: "og:title", content: "Reactive Relics — Workbench & Live Widget Catalog" },
      {
        property: "og:description",
        content:
          "A catalog of live React widgets animated by mock data derived from their REST APIs.",
      },
    ],
  }),
  component: CatalogPage,
});

function CatalogPage() {
  const { widgets, ready, reset } = useCatalog();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [q, setQ] = useState("");

  const selectedType: WidgetType | "all" = search.type || "all";

  const handleSelectType = (type: WidgetType | "all") => {
    navigate({
      search: (prev) => ({
        ...prev,
        type: type === "all" ? undefined : type,
      }),
    });
  };

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return widgets.filter((w) => {
      const widgetType = w.type || "react-component";
      const matchesType = selectedType === "all" || widgetType === selectedType;

      if (!matchesType) return false;
      if (!needle) return true;

      return [
        w.name,
        w.description,
        ...w.tags,
        ...w.inputs.map((i) => i.name),
        ...w.endpoints.map((e) => e.signature),
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [widgets, q, selectedType]);

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 space-y-8">
      {/* Workbench Hero Banner */}
      <section className="panel overflow-hidden border-border bg-surface/70">
        <div className="grid gap-6 p-6 sm:p-8 lg:grid-cols-[1.4fr_1fr] lg:items-center">
          <div>
            <div className="flex items-center gap-2">
              <span className="live-dot" />
              <p className="label-mono text-accent">relic inventory / live sandbox engine</p>
            </div>
            <h1 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
              Every relic, running live in isolation.
            </h1>
            <p className="mt-3 max-w-xl text-sm text-muted-foreground leading-relaxed">
              Mount reactive components with automatic AST input inference, mock REST streams, and
              live state isolation. Browse by relic archetype or inspect schema contracts below.
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <Link
                to="/new"
                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 font-mono text-xs font-semibold uppercase text-primary-foreground shadow-sm transition-opacity hover:opacity-90"
              >
                <Plus className="h-3.5 w-3.5" />
                Mount New Relic
              </Link>
              <button
                onClick={reset}
                className="inline-flex items-center gap-2 rounded-md border border-border bg-background/50 px-3.5 py-2 font-mono text-xs uppercase text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
              >
                <RotateCcw className="h-3.5 w-3.5" /> Reset seed relics
              </button>
            </div>
          </div>

          {/* Metric Counter Panels */}
          <dl className="grid grid-cols-3 gap-3">
            {[
              { k: "relics", v: widgets.length, sub: "mounted" },
              {
                k: "apis",
                v: new Set(widgets.flatMap((w) => w.endpoints.map((e) => e.signature))).size,
                sub: "detected",
              },
              {
                k: "props",
                v: new Set(widgets.flatMap((w) => w.inputs.map((i) => i.name))).size,
                sub: "inferred",
              },
            ].map((s) => (
              <div
                key={s.k}
                className="rounded-lg border border-border bg-background/60 p-3 sm:p-4 text-center sm:text-left transition-colors hover:border-primary/30"
              >
                <dd className="font-mono text-2xl sm:text-3xl font-bold text-primary">{s.v}</dd>
                <dt className="label-mono mt-1 text-[10px] text-muted-foreground">
                  {s.k} · {s.sub}
                </dt>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <GovernanceDemo />

      {/* Relic Types / Archetypes Grid with shadow-glow hover effects */}
      <RelicTypesGrid
        widgets={widgets}
        selectedType={selectedType}
        onSelectType={handleSelectType}
      />

      {/* Search and Filter Control Bar */}
      <div className="space-y-4">
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
          <div className="relative flex-1">
            <Search className="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search relics by name, prop, tag, or API signature…"
              className="w-full rounded-md border border-border bg-surface/80 py-2 pl-9 pr-3 font-mono text-xs text-foreground placeholder:text-muted-foreground outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </div>

          <div className="flex items-center justify-between gap-2 font-mono text-xs text-muted-foreground">
            <span className="label-mono">
              Showing {filtered.length} of {widgets.length} relics
            </span>
            {selectedType !== "all" && (
              <button
                onClick={() => handleSelectType("all")}
                className="rounded bg-muted px-2 py-1 text-[11px] text-accent hover:text-foreground"
              >
                Clear filter ×
              </button>
            )}
          </div>
        </div>

        {/* Relic Library Grid with shadow-glow card hover states */}
        {!ready ? (
          <div className="panel p-12 text-center">
            <p className="label-mono">loading workbench catalog…</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="panel p-12 text-center space-y-3">
            <p className="text-muted-foreground text-sm font-mono">
              No relics match the active criteria "{q || selectedType}".
            </p>
            <button
              onClick={() => {
                setQ("");
                handleSelectType("all");
              }}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-3 py-1.5 font-mono text-xs uppercase text-primary hover:bg-muted"
            >
              Reset Filters
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((w) => (
              <WidgetCard key={w.id} widget={w} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
