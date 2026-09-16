import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import * as React from "react";
import {
  Activity,
  ArrowLeft,
  ChevronRight,
  Database,
  Layers,
  ShieldCheck,
  Sparkles,
  Trash2,
  Zap,
} from "lucide-react";
import { useCatalog } from "@/lib/storage";
import { useHydrated } from "@/hooks/use-hydrated";
import { getCapabilityForWidget } from "@/lib/capabilities-registry";
import { CapabilityId } from "@/core/types/designIR";
import { ContractTab } from "@/components/studio/ContractTab";
import { FixtureTab } from "@/components/studio/FixtureTab";
import { AdapterTab } from "@/components/studio/AdapterTab";
import { ViewSpecTab } from "@/components/studio/ViewSpecTab";
import { toast } from "sonner";

export const Route = createFileRoute("/widget/$id")({
  head: () => ({
    meta: [
      { title: "Relic Studio & Adapter Workbench — Reactive Relics" },
      {
        name: "description",
        content:
          "Four-tab workbench for inspecting capability contracts, testing synthetic fixtures, configuring declarative data adapters, and integrating into ViewSpec surfaces.",
      },
    ],
  }),
  component: WidgetDetailPage,
});

type StudioTab = "contract" | "fixture" | "adapter" | "viewspec";

function WidgetDetailPage() {
  const { id } = Route.useParams();
  const { widgets, ready, remove } = useCatalog();
  const navigate = useNavigate();
  const hydrated = useHydrated();

  const widget = widgets.find((w) => w.id === id);

  // Tab State
  const [activeTab, setActiveTab] = React.useState<StudioTab>("contract");

  // Assigned capability contract state
  const [capability, setCapability] = React.useState<CapabilityId>(() => {
    if (widget) return getCapabilityForWidget(widget.id, widget.name);
    return "EntityCollection";
  });

  React.useEffect(() => {
    if (widget) {
      setCapability(getCapabilityForWidget(widget.id, widget.name));
    }
  }, [widget?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!ready || !hydrated) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
        <div className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
          <span className="live-dot" />
          <span>INITIALIZING STUDIO ENGINE...</span>
        </div>
      </div>
    );
  }

  if (!widget) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-20 text-center sm:px-6">
        <h1 className="text-2xl font-semibold text-foreground">Relic not found in catalog</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          The requested widget identifier "{id}" does not exist in local workbench storage.
        </p>
        <Link
          to="/"
          className="mt-6 inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 font-mono text-xs font-medium text-primary-foreground hover:bg-primary/90"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span>Return to Relic Catalog</span>
        </Link>
      </div>
    );
  }

  const handleDelete = () => {
    remove(widget.id);
    toast.success(`Removed relic ${widget.name}`);
    navigate({ to: "/" });
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 space-y-6">
      {/* Breadcrumb Navigation */}
      <div className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
        <Link to="/" className="hover:text-foreground transition-colors">
          Catalog
        </Link>
        <ChevronRight className="h-3.5 w-3.5 opacity-60" />
        <span className="text-foreground font-semibold">{widget.name}</span>
        <span className="rounded bg-surface px-1.5 py-0.2 text-[10px] text-muted-foreground border border-border/60">
          {widget.componentName}
        </span>
      </div>

      {/* Relic Master Header Bar */}
      <div className="panel p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="live-dot" />
              <span className="label-mono text-[11px] text-accent">
                STUDIO WORKBENCH // RELIC {widget.id}
              </span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-foreground">
              {widget.name}
            </h1>
            <p className="max-w-3xl text-sm text-muted-foreground leading-relaxed">
              {widget.description}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleDelete}
              className="inline-flex items-center gap-1.5 rounded-md border border-border/80 bg-surface px-3 py-2 font-mono text-xs text-muted-foreground transition-colors hover:border-destructive hover:text-destructive"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span>Unmount Relic</span>
            </button>
          </div>
        </div>

        {/* 4-Tab Navigation Bar */}
        <div className="mt-6 flex flex-wrap gap-2 border-t border-border/50 pt-4">
          <button
            type="button"
            onClick={() => setActiveTab("contract")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-2 font-mono text-xs font-semibold transition-all ${
              activeTab === "contract"
                ? "bg-primary text-primary-foreground shadow-xs ring-1 ring-primary/50"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <ShieldCheck className="h-4 w-4" />
            <span>1. Contract & Capability</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("fixture")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-2 font-mono text-xs font-semibold transition-all ${
              activeTab === "fixture"
                ? "bg-primary text-primary-foreground shadow-xs ring-1 ring-primary/50"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Activity className="h-4 w-4" />
            <span>2. Fixture Lab</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("adapter")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-2 font-mono text-xs font-semibold transition-all ${
              activeTab === "adapter"
                ? "bg-primary text-primary-foreground shadow-xs ring-1 ring-primary/50"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Database className="h-4 w-4" />
            <span>3. Data Adapter / Projection</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("viewspec")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-2 font-mono text-xs font-semibold transition-all ${
              activeTab === "viewspec"
                ? "bg-primary text-primary-foreground shadow-xs ring-1 ring-primary/50"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Layers className="h-4 w-4" />
            <span>4. ViewSpec Integration</span>
          </button>
        </div>
      </div>

      {/* Tab Content Panes */}
      <div className="transition-opacity duration-200">
        {activeTab === "contract" && (
          <ContractTab
            widget={widget}
            selectedCapability={capability}
            onSelectCapability={(cap) => {
              setCapability(cap);
              toast.success(`Assigned ${cap} capability contract`);
            }}
          />
        )}

        {activeTab === "fixture" && <FixtureTab widget={widget} selectedCapability={capability} />}

        {activeTab === "adapter" && <AdapterTab widget={widget} selectedCapability={capability} />}

        {activeTab === "viewspec" && (
          <ViewSpecTab widget={widget} selectedCapability={capability} />
        )}
      </div>
    </div>
  );
}
