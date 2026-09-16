import { createFileRoute, Link } from "@tanstack/react-router";
import * as React from "react";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Code2,
  Cpu,
  Database,
  ExternalLink,
  Layers,
  LayoutDashboard,
  Play,
  RefreshCw,
  Sliders,
  Sparkles,
  Terminal,
  Zap,
  Box,
  Eye,
  SlidersHorizontal,
} from "lucide-react";
import { DesignIR } from "@/core/types/designIR";
import { compileDesignIR } from "@/core/compiler/compiler";
import { ViewSpec, MultiSurfaceViewSpec, LayoutNode, WidgetInstance } from "@/core/types/viewSpec";
import { InMemoryContractStateStore, ContractStateStore } from "@/core/runtime/contractState";
import { DefaultActionInterpreter } from "@/core/runtime/actionInterpreter";
import { SimpleEventBus } from "@/core/runtime/eventBus";
import { RuntimeView } from "@/core/runtime/types";
import { generateRuntimeMockData } from "@/core/runtime/mockData";
import {
  DefaultInteractionContextStore,
  InteractionContext,
} from "@/core/runtime/interactionContext";
import { DefaultOperatorPersona, OperatorPersonaAPI } from "@/core/runtime/operatorPersona";
import { resolveDocumentation, DocumentationEntry } from "@/core/runtime/documentationRegistry";
import { OperatorPanel } from "@/components/runtime/OperatorPanel";
import { HelpDrawer } from "@/components/runtime/HelpDrawer";
import { HelpVideoOverlay } from "@/components/runtime/HelpVideoOverlay";
import { ContextTab } from "@/components/studio/ContextTab";
import { CAPABILITY_REGISTRY } from "@/lib/capabilities-registry";
import { useCatalog } from "@/lib/storage";
import { WidgetSandbox } from "@/components/WidgetSandbox";
import { Widget } from "@/lib/widget-types";
import { toast } from "sonner";

export const Route = createFileRoute("/viewspec")({
  head: () => ({
    meta: [
      { title: "ViewSpec Surface Studio — Reactive Relics" },
      {
        name: "description",
        content:
          "Pure DesignIR compiler, spatial layout synthesizer, payload-to-contract adapter projections, and reactive ContractStateStore runtime.",
      },
    ],
  }),
  component: ViewSpecStudioPage,
});

/**
 * Canonical Invariant 5:
 * Author all presets as DesignIR documents that compile into ViewSpecs,
 * rather than hand-written ViewSpecs.
 */
const IR_PRESETS: Array<{ id: string; name: string; description: string; ir: DesignIR }> = [
  {
    id: "execution-switchboard",
    name: "Nebula Execution Switchboard",
    description:
      "Mission control dashboard with KPI header, status pipeline, sidebar inspector, and audit log.",
    ir: {
      id: "ir-execution-switchboard",
      name: "Nebula Execution Switchboard",
      description:
        "Mission control switchboard for distributed requirement compilation and worker lease locks.",
      roles: {
        kpiMetrics: {
          label: "System Health Matrix",
          capability: { id: "KeyMetricMatrix" },
          priority: "primary",
          density: "highSalience",
          constraints: { layoutBias: "header" },
          interactions: ["inspect"],
        },
        pipeline: {
          label: "Execution Pipeline",
          capability: { id: "StatusBoard" },
          priority: "primary",
          density: "normal",
          constraints: { layoutBias: "main" },
          interactions: ["inspect", "select"],
        },
        inspector: {
          label: "Contract State Inspector",
          capability: { id: "InspectorPanel" },
          priority: "secondary",
          density: "normal",
          constraints: { layoutBias: "sidebar" },
          interactions: ["acknowledge", "dismiss"],
        },
        auditFeed: {
          label: "Audit & Event Stream",
          capability: { id: "AuditStream" },
          priority: "ambient",
          density: "compact",
          constraints: { layoutBias: "footer" },
          interactions: ["inspect"],
        },
      },
      hierarchy: {
        primaryRoles: ["kpiMetrics", "pipeline"],
        secondaryRoles: ["inspector"],
        ambientRoles: ["auditFeed"],
      },
      interactions: [
        { verb: "inspect", sourceRole: "pipeline", targetRole: "inspector", scope: "crossRole" },
        { verb: "inspect", sourceRole: "kpiMetrics", targetRole: "inspector", scope: "crossRole" },
        {
          verb: "acknowledge",
          sourceRole: "inspector",
          targetRole: "pipeline",
          scope: "crossRole",
        },
      ],
      density: "normal",
      context: {
        surfaceType: "dashboard",
        timeSensitivity: "nearRealTime",
        reliabilityBias: "strong",
      },
    },
  },
  {
    id: "incident-consensus",
    name: "Consensus & Deliberation Matrix",
    description:
      "High-integrity multi-agent voting and resolution matrix with real-time audit feed.",
    ir: {
      id: "ir-incident-consensus",
      name: "Consensus & Deliberation Matrix",
      description: "Quorum resolution for AST transformations and security policies.",
      roles: {
        summaryKPI: {
          label: "Quorum KPIs",
          capability: { id: "KeyMetricMatrix" },
          priority: "primary",
          density: "compact",
          constraints: { layoutBias: "header" },
        },
        consensus: {
          label: "Consensus Deliberation",
          capability: { id: "ConsensusMatrix" },
          priority: "primary",
          density: "normal",
          constraints: { layoutBias: "main" },
          interactions: ["inspect", "compare"],
        },
        history: {
          label: "Quorum Timeline",
          capability: { id: "Timeline" },
          priority: "secondary",
          density: "normal",
          constraints: { layoutBias: "sidebar" },
        },
        audit: {
          label: "Resolution Stream",
          capability: { id: "AuditStream" },
          priority: "ambient",
          density: "compact",
          constraints: { layoutBias: "footer" },
        },
      },
      hierarchy: {
        primaryRoles: ["summaryKPI", "consensus"],
        secondaryRoles: ["history"],
        ambientRoles: ["audit"],
      },
      interactions: [
        { verb: "inspect", sourceRole: "consensus", targetRole: "history", scope: "crossRole" },
      ],
      density: "normal",
      context: {
        surfaceType: "inspector",
        timeSensitivity: "realTime",
        reliabilityBias: "strict",
      },
    },
  },
  {
    id: "spec-timeline",
    name: "Bitemporal Spec Timeline",
    description:
      "Chronological and validity interval diff view with entity collection and inspector.",
    ir: {
      id: "ir-spec-timeline",
      name: "Bitemporal Spec Timeline",
      description: "Tracks schema revisions across transaction time and valid time.",
      roles: {
        timeline: {
          label: "Spec Revisions",
          capability: { id: "Timeline" },
          priority: "primary",
          density: "normal",
          constraints: { layoutBias: "main" },
          interactions: ["inspect", "select"],
        },
        entities: {
          label: "Cluster Entity Inventory",
          capability: { id: "EntityCollection" },
          priority: "secondary",
          density: "compact",
          constraints: { layoutBias: "sidebar" },
          interactions: ["inspect"],
        },
        inspector: {
          label: "Revision Detail",
          capability: { id: "InspectorPanel" },
          priority: "ambient",
          density: "compact",
          constraints: { layoutBias: "footer" },
        },
      },
      hierarchy: {
        primaryRoles: ["timeline"],
        secondaryRoles: ["entities"],
        ambientRoles: ["inspector"],
      },
      interactions: [
        { verb: "inspect", sourceRole: "timeline", targetRole: "inspector", scope: "crossRole" },
        { verb: "inspect", sourceRole: "entities", targetRole: "inspector", scope: "crossRole" },
      ],
      density: "normal",
      context: {
        surfaceType: "timelineView",
        timeSensitivity: "historical",
        reliabilityBias: "strong",
      },
    },
  },
  {
    id: "telemetry-fleet",
    name: "Telemetry & Fleet Health",
    description: "Metric series trends, work queues, and system utilization gauges.",
    ir: {
      id: "ir-telemetry-fleet",
      name: "Telemetry & Fleet Health",
      description: "Real-time cluster telemetry monitoring and work queue execution.",
      roles: {
        kpi: {
          label: "Throughput Gauges",
          capability: { id: "KeyMetricMatrix" },
          priority: "primary",
          density: "highSalience",
          constraints: { layoutBias: "header" },
        },
        series: {
          label: "Latency Sparklines",
          capability: { id: "MetricSeries" },
          priority: "primary",
          density: "normal",
          constraints: { layoutBias: "main" },
        },
        queue: {
          label: "Active Work Queue",
          capability: { id: "WorkQueue" },
          priority: "secondary",
          density: "normal",
          constraints: { layoutBias: "sidebar" },
          interactions: ["inspect"],
        },
      },
      hierarchy: {
        primaryRoles: ["kpi", "series"],
        secondaryRoles: ["queue"],
      },
      interactions: [
        { verb: "inspect", sourceRole: "queue", targetRole: "series", scope: "crossRole" },
      ],
      density: "normal",
      context: {
        surfaceType: "dashboard",
        timeSensitivity: "realTime",
        reliabilityBias: "eventual",
      },
    },
  },
  {
    id: "incident-multi-surface-workflow",
    name: "Multi-Surface Incident Workflow",
    description:
      "Multi-surface triage workflow across Overview, Drilldown, and Deliberation surfaces.",
    ir: {
      id: "ir-incident-multi-surface-workflow",
      name: "Distributed Incident Multi-Surface System",
      description: "Cross-surface workflow orchestration with declarative state routing.",
      globalContext: {
        timeSensitivity: "realTime",
        reliabilityBias: "strict",
      },
      surfaces: {
        overview: {
          id: "overview",
          name: "Global Incident Overview",
          kind: "dashboard",
          roles: {
            kpi: {
              label: "System Health Summary",
              capability: { id: "KeyMetricMatrix" },
              priority: "primary",
              density: "highSalience",
              constraints: { layoutBias: "header" },
            },
            pipeline: {
              label: "Active Incidents Pipeline",
              capability: { id: "StatusBoard" },
              priority: "primary",
              density: "normal",
              constraints: { layoutBias: "main" },
              interactions: ["inspect", "navigate"],
            },
            stream: {
              label: "Global Audit Log",
              capability: { id: "AuditStream" },
              priority: "ambient",
              density: "compact",
              constraints: { layoutBias: "footer" },
            },
          },
          hierarchy: {
            primaryRoles: ["kpi", "pipeline"],
            ambientRoles: ["stream"],
          },
          interactions: [
            {
              verb: "navigate",
              sourceRole: "pipeline",
              targetSurface: "investigation",
              scope: "crossSurface",
            },
          ],
        },
        investigation: {
          id: "investigation",
          name: "Incident Root Cause Analysis",
          kind: "workbench",
          roles: {
            timeline: {
              label: "Failure Event Timeline",
              capability: { id: "Timeline" },
              priority: "primary",
              density: "normal",
              constraints: { layoutBias: "main" },
              interactions: ["inspect"],
            },
            inspector: {
              label: "Stack State Inspector",
              capability: { id: "InspectorPanel" },
              priority: "secondary",
              density: "normal",
              constraints: { layoutBias: "sidebar" },
            },
          },
          hierarchy: {
            primaryRoles: ["timeline"],
            secondaryRoles: ["inspector"],
          },
          interactions: [
            {
              verb: "inspect",
              sourceRole: "timeline",
              targetRole: "inspector",
              scope: "crossRole",
            },
          ],
        },
      },
      workflows: [
        {
          id: "triage-to-resolution",
          name: "Incident Triage Workflow",
          description: "Guides operator from high-level triage to drilldown inspection.",
          steps: [
            {
              id: "step-1",
              name: "Review System Outages",
              surfaceId: "overview",
              focusRoleId: "pipeline",
            },
            {
              id: "step-2",
              name: "Deep Dive Failure Timeline",
              surfaceId: "investigation",
              focusRoleId: "timeline",
            },
          ],
        },
      ],
    },
  },
];

type StudioTab =
  "surface" | "viewspec" | "adapters" | "stores" | "eventbus" | "designir" | "context";

export function ViewSpecStudioPage() {
  const { widgets } = useCatalog();

  // Selected preset and editable IR string
  const [selectedPresetId, setSelectedPresetId] = React.useState("execution-switchboard");
  const [irText, setIrText] = React.useState<string>(() =>
    JSON.stringify(IR_PRESETS[0].ir, null, 2),
  );

  const [activeTab, setActiveTab] = React.useState<StudioTab>("surface");
  const [eventLogs, setEventLogs] = React.useState<
    Array<{ id: string; timestamp: string; verb: string; sourceNode: string; payload: unknown }>
  >([]);

  /**
   * Canonical Invariant:
   * DesignIR compiler returns MultiSurfaceViewSpec as a pure deterministic AST.
   */
  const { multiViewSpec, viewSpec, compileError } = React.useMemo<{
    multiViewSpec: MultiSurfaceViewSpec | null;
    viewSpec: ViewSpec | null;
    compileError: string | null;
  }>(() => {
    try {
      const parsedIR = JSON.parse(irText) as DesignIR;
      const compiled = compileDesignIR(parsedIR);
      const activeSurface =
        compiled.surfaces[compiled.activeSurfaceId || Object.keys(compiled.surfaces)[0]] ||
        Object.values(compiled.surfaces)[0];
      return { multiViewSpec: compiled, viewSpec: activeSurface || null, compileError: null };
    } catch (err) {
      return { multiViewSpec: null, viewSpec: null, compileError: (err as Error).message };
    }
  }, [irText]);

  /**
   * Canonical Invariant:
   * Route all event-driven state changes through ContractStateStore, not directly into widget props.
   * Runtime generates fixtures for stores.
   */
  const storesRef = React.useRef<Map<string, InMemoryContractStateStore<unknown>>>(new Map());
  const [storeVersion, setStoreVersion] = React.useState(0);

  // Initialize or re-initialize ContractStateStores whenever viewSpec re-compiles
  React.useEffect(() => {
    if (!viewSpec) return;
    const newStores = new Map<string, InMemoryContractStateStore<unknown>>();

    for (const widgetInstance of viewSpec.widgets) {
      const fixtureData = generateRuntimeMockData(
        widgetInstance.widget.contract,
        widgetInstance.role || widgetInstance.id,
      );
      const store = new InMemoryContractStateStore(fixtureData);
      newStores.set(widgetInstance.id, store);
    }

    storesRef.current = newStores;
    setStoreVersion((v) => v + 1);
  }, [viewSpec]);

  const eventBus = React.useMemo(() => new SimpleEventBus(), []);
  const actionInterpreter = React.useMemo(() => new DefaultActionInterpreter(), []);

  // Ephemeral Operator Context and Persona instances
  const contextStore = React.useMemo(
    () => new DefaultInteractionContextStore("execution-switchboard"),
    [],
  );

  const [interactionContext, setInteractionContext] = React.useState<InteractionContext>(() =>
    contextStore.get(),
  );

  const operator = React.useMemo(
    () =>
      new DefaultOperatorPersona(
        contextStore,
        storesRef.current,
        () => multiViewSpec || viewSpec || undefined,
      ),
    [contextStore, multiViewSpec, viewSpec],
  );

  // Sync interaction context changes
  React.useEffect(() => {
    const unsubscribe = contextStore.subscribe((ctx) => {
      setInteractionContext(ctx);
    });
    return () => unsubscribe();
  }, [contextStore]);

  // Update surface navigation on viewSpec switch
  React.useEffect(() => {
    if (viewSpec) {
      contextStore.onSurfaceNavigate(viewSpec.surfaceId || viewSpec.id || "main");
    }
  }, [viewSpec, contextStore]);

  // Manual / Help mode states
  const [helpDrawerOpen, setHelpDrawerOpen] = React.useState(false);
  const [helpVideoOpen, setHelpVideoOpen] = React.useState(false);
  const [currentHelpEntry, setCurrentHelpEntry] = React.useState<DocumentationEntry>(() =>
    resolveDocumentation({ capabilityId: "KeyMetricMatrix" }),
  );

  const handleOpenDrawerHelp = React.useCallback(
    (targetWidgetId?: string, capabilityId?: string) => {
      const entry = resolveDocumentation({
        targetWidgetId,
        capabilityId,
        surfaceId: viewSpec?.surfaceId || viewSpec?.id,
      });
      setCurrentHelpEntry(entry);
      setHelpDrawerOpen(true);
      operator.openHelp("drawer", targetWidgetId);
    },
    [viewSpec, operator],
  );

  const handleOpenVideoHelp = React.useCallback(
    (targetWidgetId?: string, capabilityId?: string) => {
      const entry = resolveDocumentation({
        targetWidgetId,
        capabilityId,
        surfaceId: viewSpec?.surfaceId || viewSpec?.id,
      });
      setCurrentHelpEntry(entry);
      setHelpVideoOpen(true);
      operator.openHelp("video", targetWidgetId);
    },
    [viewSpec, operator],
  );

  // Handle preset selection
  const handleSelectPreset = (presetId: string) => {
    setSelectedPresetId(presetId);
    const preset = IR_PRESETS.find((p) => p.id === presetId);
    if (preset) {
      setIrText(JSON.stringify(preset.ir, null, 2));
      contextStore.onSurfaceNavigate(preset.id);
      operator.clearHighlights();
      operator.narrate(`Loaded "${preset.name}" preset surface.`, "info");
      toast.success(`Loaded "${preset.name}" DesignIR`);
    }
  };

  /**
   * Dispatch interaction verb across the reactive bus.
   * ActionInterpreter routes mutations strictly through the target ContractStateStore!
   */
  const handleDispatchEvent = React.useCallback(
    async (verb: string, sourceWidgetId: string, payload: unknown) => {
      if (!viewSpec) return;

      const logEntry = {
        id: Math.random().toString(36).substring(7),
        timestamp: new Date().toISOString().split("T")[1].slice(0, 8),
        verb,
        sourceNode: sourceWidgetId,
        payload,
      };
      setEventLogs((prev) => [logEntry, ...prev.slice(0, 24)]);

      // Notify InteractionContextStore event hook
      contextStore.onEventDispatch(sourceWidgetId, verb, payload);

      // Create a lightweight RuntimeView mock for the action interpreter
      const runtimeView: RuntimeView = {
        spec: viewSpec,
        widgets: new Map(),
        adapters: new Map(),
        contractStores: storesRef.current as Map<string, ContractStateStore>,
        layout: {
          nodes: new Map(),
          regions: {
            main: document.createElement("div"),
            sidebar: document.createElement("div"),
            header: document.createElement("div"),
            footer: document.createElement("div"),
            overlay: document.createElement("div"),
          },
        },
        eventBus,
        container: document.createElement("div"),
        mounted: true,
      };

      // Match event routes from the compiled ViewSpec
      const matchingRoutes = viewSpec.events.filter(
        (r) => r.fromWidget === sourceWidgetId && r.event === verb,
      );

      if (matchingRoutes.length > 0) {
        for (const route of matchingRoutes) {
          await actionInterpreter.execute(route.action, runtimeView, payload);
        }
      } else {
        // Fallback: If verb is inspect and an inspector panel exists, route to inspector's ContractStateStore
        const inspectorWidget = viewSpec.widgets.find(
          (w) => w.widget.contract === "InspectorPanel",
        );
        if (verb === "inspect" && inspectorWidget) {
          const store = storesRef.current.get(inspectorWidget.id);
          if (store) {
            const current = (store.get() || {}) as Record<string, unknown>;
            store.set({
              ...current,
              target: payload,
              selected: true,
              inspectedAt: Date.now(),
            });
          }
        }
      }

      setStoreVersion((v) => v + 1);
    },
    [viewSpec, eventBus, actionInterpreter, contextStore],
  );

  // Group nodes by spatial region
  const nodesByRegion = React.useMemo(() => {
    if (!viewSpec) return { header: [], main: [], sidebar: [], footer: [], overlay: [] };
    const groups: Record<LayoutNode["region"], LayoutNode[]> = {
      header: [],
      main: [],
      sidebar: [],
      footer: [],
      overlay: [],
    };
    for (const node of viewSpec.layout.nodes) {
      if (groups[node.region]) {
        groups[node.region].push(node);
      }
    }
    return groups;
  }, [viewSpec]);

  const operatorHighlights = operator.getHighlights();

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 space-y-6">
      {/* Studio Header */}
      <div className="panel p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="live-dot" />
              <span className="label-mono text-[11px] text-primary">
                NEXUS // VISION CANONICAL ARCHITECTURE
              </span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-foreground">
              ViewSpec Surface Studio
            </h1>
            <p className="max-w-3xl text-sm text-muted-foreground leading-relaxed">
              Pure deterministic compiler transforms semantic <strong>DesignIR</strong> into an
              executable <strong>ViewSpec program</strong>. All event-driven state mutations route
              strictly through <strong>ContractStateStore</strong>, and adapters serve as decoupled{" "}
              <strong>payload → capability contract projections</strong>.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <span
              className={`rounded-md border px-3 py-1.5 font-mono text-xs font-semibold ${
                compileError
                  ? "border-destructive/60 bg-destructive/15 text-destructive"
                  : "border-primary/40 bg-primary/10 text-primary"
              }`}
            >
              COMPILER: {compileError ? "SYNTAX_ERROR" : "PURE_DETERMINISTIC_OK"}
            </span>
          </div>
        </div>

        {/* Preset Selector */}
        <div className="pt-3 border-t border-border/40 space-y-2">
          <div className="label-mono text-[11px] text-muted-foreground">
            Author Presets (DesignIR Source Documents):
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
            {IR_PRESETS.map((p) => {
              const isSelected = selectedPresetId === p.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => handleSelectPreset(p.id)}
                  className={`p-3 rounded-lg border text-left transition-all text-xs font-mono ${
                    isSelected
                      ? "border-primary bg-primary/15 text-primary font-bold shadow-xs ring-1 ring-primary/40"
                      : "border-border/60 bg-surface/60 text-muted-foreground hover:bg-surface hover:text-foreground"
                  }`}
                >
                  <div className="font-sans font-bold text-foreground text-xs">{p.name}</div>
                  <div className="text-[10px] text-muted-foreground font-sans line-clamp-2 mt-1">
                    {p.description}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Studio Tabs */}
        <div className="flex flex-wrap gap-2 pt-3 border-t border-border/40">
          <button
            type="button"
            onClick={() => setActiveTab("surface")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-1.5 font-mono text-xs font-semibold transition-all ${
              activeTab === "surface"
                ? "bg-primary text-primary-foreground shadow-xs"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <LayoutDashboard className="h-4 w-4" />
            <span>1. Live Surface Canvas</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("viewspec")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-1.5 font-mono text-xs font-semibold transition-all ${
              activeTab === "viewspec"
                ? "bg-primary text-primary-foreground shadow-xs"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Layers className="h-4 w-4" />
            <span>2. Compiled ViewSpec Program AST</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("adapters")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-1.5 font-mono text-xs font-semibold transition-all ${
              activeTab === "adapters"
                ? "bg-primary text-primary-foreground shadow-xs"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Database className="h-4 w-4" />
            <span>3. Payload → Contract Projections ({viewSpec?.adapters.length || 0})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("stores")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-1.5 font-mono text-xs font-semibold transition-all ${
              activeTab === "stores"
                ? "bg-primary text-primary-foreground shadow-xs"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Box className="h-4 w-4" />
            <span>4. ContractStateStores ({storesRef.current.size})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("eventbus")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-1.5 font-mono text-xs font-semibold transition-all ${
              activeTab === "eventbus"
                ? "bg-primary text-primary-foreground shadow-xs"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Terminal className="h-4 w-4" />
            <span>5. Event Routing Matrix ({eventLogs.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("designir")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-1.5 font-mono text-xs font-semibold transition-all ${
              activeTab === "designir"
                ? "bg-primary text-primary-foreground shadow-xs"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Code2 className="h-4 w-4" />
            <span>6. DesignIR Document Source</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("context")}
            className={`flex items-center gap-2 rounded-md px-3.5 py-1.5 font-mono text-xs font-semibold transition-all ${
              activeTab === "context"
                ? "bg-accent text-accent-foreground shadow-xs font-bold"
                : "bg-surface text-muted-foreground hover:bg-surface/80 hover:text-foreground border border-border/60"
            }`}
          >
            <Sparkles className="h-4 w-4 text-accent" />
            <span>7. Operator & Interaction Context</span>
          </button>
        </div>
      </div>

      {/* Compile Error Warning */}
      {compileError && (
        <div className="panel border-destructive/60 bg-destructive/10 p-4 text-xs font-mono text-destructive">
          <strong>COMPILATION ERROR:</strong> {compileError}
        </div>
      )}

      {/* Tab 1: Live Interactive Multi-Widget Surface */}
      {activeTab === "surface" && viewSpec && (
        <div className="space-y-6">
          {/* Operator HUD Presence */}
          <OperatorPanel
            operator={operator}
            contextStore={contextStore}
            onOpenHelpDrawer={(widgetId) => {
              const inst = viewSpec.widgets.find((w) => w.id === widgetId);
              handleOpenDrawerHelp(widgetId, inst?.widget.contract);
            }}
            onOpenHelpVideo={(widgetId) => {
              const inst = viewSpec.widgets.find((w) => w.id === widgetId);
              handleOpenVideoHelp(widgetId, inst?.widget.contract);
            }}
          />

          <div className="panel p-4 flex flex-wrap items-center justify-between gap-3 bg-surface/50">
            <div className="flex items-center gap-2 font-mono text-xs">
              <span className="live-dot" />
              <span className="text-foreground font-semibold">SURFACE // {viewSpec.name}</span>
              <span className="text-muted-foreground text-[11px]">
                ({viewSpec.layout.nodes.length} spatial nodes · {viewSpec.widgets.length} contract
                stores)
              </span>
            </div>
            <div className="flex items-center gap-3 font-mono text-[10px] text-muted-foreground">
              <span>SURFACE_TYPE: {viewSpec.context?.surfaceType || "dashboard"}</span>
              <span>RELIABILITY: {viewSpec.context?.reliabilityBias || "strong"}</span>
              <span className="text-primary font-semibold">
                ACTIVE_WIDGET: {interactionContext.activeWidgetId || "none"}
              </span>
            </div>
          </div>

          {/* Spatial Layout Matrix */}
          <div className="space-y-6">
            {/* Header Region */}
            {nodesByRegion.header.length > 0 && (
              <div className="space-y-2">
                <div className="label-mono text-[10px] text-muted-foreground">REGION: HEADER</div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                  {nodesByRegion.header.map((node) => {
                    const widgetInstance = viewSpec.widgets.find((w) => w.id === node.widgetId);
                    if (!widgetInstance) return null;
                    const store = storesRef.current.get(widgetInstance.id);
                    const isHighlighted = operatorHighlights.has(widgetInstance.id);
                    const highlightMsg = operatorHighlights.get(widgetInstance.id);
                    return (
                      <ReactiveSurfaceNode
                        key={node.id}
                        node={node}
                        widgetInstance={widgetInstance}
                        store={store}
                        storeVersion={storeVersion}
                        widgets={widgets}
                        isHighlighted={isHighlighted}
                        highlightMessage={highlightMsg}
                        isActiveWidget={interactionContext.activeWidgetId === widgetInstance.id}
                        onWidgetClick={() =>
                          contextStore.onWidgetClick(
                            widgetInstance.id,
                            widgetInstance.role || widgetInstance.id,
                          )
                        }
                        onFocus={() => contextStore.onWidgetFocus(widgetInstance.id)}
                        onBlur={() => contextStore.onWidgetBlur(widgetInstance.id)}
                        onOpenHelp={(mode) =>
                          mode === "drawer"
                            ? handleOpenDrawerHelp(
                                widgetInstance.id,
                                widgetInstance.widget.contract,
                              )
                            : handleOpenVideoHelp(widgetInstance.id, widgetInstance.widget.contract)
                        }
                        onDispatch={(verb, payload) =>
                          handleDispatchEvent(verb, widgetInstance.id, payload)
                        }
                      />
                    );
                  })}
                </div>
              </div>
            )}

            {/* Main & Sidebar Regions */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
              {/* Main Content */}
              <div
                className={`${
                  nodesByRegion.sidebar.length > 0 ? "lg:col-span-8" : "lg:col-span-12"
                } space-y-4`}
              >
                <div className="label-mono text-[10px] text-muted-foreground">REGION: MAIN</div>
                <div className="space-y-4">
                  {nodesByRegion.main.map((node) => {
                    const widgetInstance = viewSpec.widgets.find((w) => w.id === node.widgetId);
                    if (!widgetInstance) return null;
                    const store = storesRef.current.get(widgetInstance.id);
                    const isHighlighted = operatorHighlights.has(widgetInstance.id);
                    const highlightMsg = operatorHighlights.get(widgetInstance.id);
                    return (
                      <ReactiveSurfaceNode
                        key={node.id}
                        node={node}
                        widgetInstance={widgetInstance}
                        store={store}
                        storeVersion={storeVersion}
                        widgets={widgets}
                        isHighlighted={isHighlighted}
                        highlightMessage={highlightMsg}
                        isActiveWidget={interactionContext.activeWidgetId === widgetInstance.id}
                        onWidgetClick={() =>
                          contextStore.onWidgetClick(
                            widgetInstance.id,
                            widgetInstance.role || widgetInstance.id,
                          )
                        }
                        onFocus={() => contextStore.onWidgetFocus(widgetInstance.id)}
                        onBlur={() => contextStore.onWidgetBlur(widgetInstance.id)}
                        onOpenHelp={(mode) =>
                          mode === "drawer"
                            ? handleOpenDrawerHelp(
                                widgetInstance.id,
                                widgetInstance.widget.contract,
                              )
                            : handleOpenVideoHelp(widgetInstance.id, widgetInstance.widget.contract)
                        }
                        onDispatch={(verb, payload) =>
                          handleDispatchEvent(verb, widgetInstance.id, payload)
                        }
                      />
                    );
                  })}
                </div>
              </div>

              {/* Sidebar */}
              {nodesByRegion.sidebar.length > 0 && (
                <div className="lg:col-span-4 space-y-4">
                  <div className="label-mono text-[10px] text-muted-foreground">
                    REGION: SIDEBAR
                  </div>
                  <div className="space-y-4">
                    {nodesByRegion.sidebar.map((node) => {
                      const widgetInstance = viewSpec.widgets.find((w) => w.id === node.widgetId);
                      if (!widgetInstance) return null;
                      const store = storesRef.current.get(widgetInstance.id);
                      const isHighlighted = operatorHighlights.has(widgetInstance.id);
                      const highlightMsg = operatorHighlights.get(widgetInstance.id);
                      return (
                        <ReactiveSurfaceNode
                          key={node.id}
                          node={node}
                          widgetInstance={widgetInstance}
                          store={store}
                          storeVersion={storeVersion}
                          widgets={widgets}
                          isHighlighted={isHighlighted}
                          highlightMessage={highlightMsg}
                          isActiveWidget={interactionContext.activeWidgetId === widgetInstance.id}
                          onWidgetClick={() =>
                            contextStore.onWidgetClick(
                              widgetInstance.id,
                              widgetInstance.role || widgetInstance.id,
                            )
                          }
                          onFocus={() => contextStore.onWidgetFocus(widgetInstance.id)}
                          onBlur={() => contextStore.onWidgetBlur(widgetInstance.id)}
                          onOpenHelp={(mode) =>
                            mode === "drawer"
                              ? handleOpenDrawerHelp(
                                  widgetInstance.id,
                                  widgetInstance.widget.contract,
                                )
                              : handleOpenVideoHelp(
                                  widgetInstance.id,
                                  widgetInstance.widget.contract,
                                )
                          }
                          onDispatch={(verb, payload) =>
                            handleDispatchEvent(verb, widgetInstance.id, payload)
                          }
                        />
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* Footer Region */}
            {nodesByRegion.footer.length > 0 && (
              <div className="space-y-2">
                <div className="label-mono text-[10px] text-muted-foreground">REGION: FOOTER</div>
                <div className="grid grid-cols-1 gap-4">
                  {nodesByRegion.footer.map((node) => {
                    const widgetInstance = viewSpec.widgets.find((w) => w.id === node.widgetId);
                    if (!widgetInstance) return null;
                    const store = storesRef.current.get(widgetInstance.id);
                    const isHighlighted = operatorHighlights.has(widgetInstance.id);
                    const highlightMsg = operatorHighlights.get(widgetInstance.id);
                    return (
                      <ReactiveSurfaceNode
                        key={node.id}
                        node={node}
                        widgetInstance={widgetInstance}
                        store={store}
                        storeVersion={storeVersion}
                        widgets={widgets}
                        isHighlighted={isHighlighted}
                        highlightMessage={highlightMsg}
                        isActiveWidget={interactionContext.activeWidgetId === widgetInstance.id}
                        onWidgetClick={() =>
                          contextStore.onWidgetClick(
                            widgetInstance.id,
                            widgetInstance.role || widgetInstance.id,
                          )
                        }
                        onFocus={() => contextStore.onWidgetFocus(widgetInstance.id)}
                        onBlur={() => contextStore.onWidgetBlur(widgetInstance.id)}
                        onOpenHelp={(mode) =>
                          mode === "drawer"
                            ? handleOpenDrawerHelp(
                                widgetInstance.id,
                                widgetInstance.widget.contract,
                              )
                            : handleOpenVideoHelp(widgetInstance.id, widgetInstance.widget.contract)
                        }
                        onDispatch={(verb, payload) =>
                          handleDispatchEvent(verb, widgetInstance.id, payload)
                        }
                      />
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 2: Compiled ViewSpec Program AST */}
      {activeTab === "viewspec" && viewSpec && (
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <div className="space-y-0.5">
              <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Layers className="h-4 w-4 text-primary" />
                <span>Compiled ViewSpec Program AST</span>
              </h3>
              <p className="text-xs text-muted-foreground">
                Canonical Invariant: ViewSpec is treated as a compiled executable program, not just
                a node configuration.
              </p>
            </div>
            <span className="font-mono text-[10px] text-accent">ID: {viewSpec.id}</span>
          </div>
          <pre className="max-h-[600px] overflow-auto rounded-lg border border-border/60 bg-background/80 p-4 font-mono text-xs text-foreground/90 leading-relaxed custom-scrollbar">
            {JSON.stringify(viewSpec, null, 2)}
          </pre>
        </div>
      )}

      {/* Tab 3: Payload → Contract Projections (Adapters) */}
      {activeTab === "adapters" && viewSpec && (
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <div className="space-y-0.5">
              <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Database className="h-4 w-4 text-primary" />
                <span>Payload → Capability Contract Projections</span>
              </h3>
              <p className="text-xs text-muted-foreground">
                Canonical Invariant: Adapters are payload → capability contract projections, NOT
                REST → widget bindings.
              </p>
            </div>
            <span className="font-mono text-[10px] text-accent">
              {viewSpec.adapters.length} Bound Projections
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {viewSpec.adapters.map((adapter) => {
              return (
                <div
                  key={adapter.adapterId}
                  className="rounded-lg border border-border/60 bg-background/80 p-4 space-y-3 font-mono text-xs"
                >
                  <div className="flex items-center justify-between border-b border-border/40 pb-2">
                    <span className="font-bold text-foreground">{adapter.adapterId}</span>
                    <span className="rounded bg-primary/15 px-2 py-0.5 text-[10px] font-semibold text-primary">
                      {adapter.outputContract}
                    </span>
                  </div>

                  <div className="space-y-1 text-[11px] text-muted-foreground">
                    <div>
                      <span className="text-foreground">Binding ID:</span> {adapter.adapterId}
                    </div>
                    <div>
                      <span className="text-foreground">Target Widget:</span> {adapter.widgetId}
                    </div>
                    {adapter.stub && (
                      <div className="pt-1">
                        <span className="text-foreground font-semibold">
                          Compiler Declarative Stub:
                        </span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {adapter.stub.steps.map((s, idx) => (
                            <span
                              key={idx}
                              className="bg-surface/90 border border-border/50 text-accent px-1.5 py-0.5 rounded text-[9px]"
                            >
                              {s.op}({JSON.stringify(s.args || {})})
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="space-y-1">
                    <div className="text-[10px] text-muted-foreground">
                      Adapter Declarative Definition:
                    </div>
                    <pre className="max-h-36 overflow-auto rounded bg-surface/60 p-2 text-[10px] text-foreground custom-scrollbar">
                      {JSON.stringify(adapter.stub, null, 2)}
                    </pre>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Tab 4: Live ContractStateStores */}
      {activeTab === "stores" && viewSpec && (
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <div className="space-y-0.5">
              <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Box className="h-4 w-4 text-primary" />
                <span>Live ContractStateStores Registry</span>
              </h3>
              <p className="text-xs text-muted-foreground">
                Canonical Invariant: All event-driven state mutations flow strictly through
                ContractStateStore instances.
              </p>
            </div>
            <span className="font-mono text-[10px] text-accent">
              Active Stores: {storesRef.current.size}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {viewSpec.widgets.map((widgetInstance) => {
              const store = storesRef.current.get(widgetInstance.id);
              const state = store ? store.get() : {};
              return (
                <div
                  key={widgetInstance.id}
                  className="rounded-lg border border-border/60 bg-background/80 p-4 space-y-3 font-mono text-xs"
                >
                  <div className="flex items-center justify-between border-b border-border/40 pb-2">
                    <div className="flex items-center gap-2">
                      <span className="live-dot" />
                      <span className="font-bold text-foreground">{widgetInstance.id}</span>
                    </div>
                    <span className="rounded bg-accent/15 px-2 py-0.5 text-[10px] text-accent font-semibold">
                      {widgetInstance.widget.contract}
                    </span>
                  </div>

                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                      <span>Live Contract State in Store:</span>
                      <button
                        type="button"
                        onClick={() => {
                          if (store) {
                            store.set({
                              lastPatched: Date.now(),
                              manualTrigger: true,
                            });
                            setStoreVersion((v) => v + 1);
                            toast.success(`Patched state for ${widgetInstance.id}`);
                          }
                        }}
                        className="text-primary hover:underline"
                      >
                        + Inject Test Patch
                      </button>
                    </div>
                    <pre className="max-h-48 overflow-auto rounded bg-surface/60 p-2.5 text-[10px] text-foreground custom-scrollbar">
                      {JSON.stringify(state, null, 2)}
                    </pre>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Tab 5: Event Bus Stream */}
      {activeTab === "eventbus" && (
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Terminal className="h-4 w-4 text-signal" />
              <span>Decoupled Reactive Event Routing Log</span>
            </h3>
            <button
              type="button"
              onClick={() => setEventLogs([])}
              className="font-mono text-xs text-muted-foreground hover:text-foreground"
            >
              Clear Logs
            </button>
          </div>

          <div className="space-y-2">
            {eventLogs.length === 0 ? (
              <div className="p-8 text-center text-xs font-mono text-muted-foreground">
                No events recorded yet. Click or interact with widgets in the Live Surface Canvas
                tab to trigger reactive events.
              </div>
            ) : (
              eventLogs.map((log) => (
                <div
                  key={log.id}
                  className="rounded-lg border border-border/60 bg-background/80 p-3 font-mono text-xs space-y-1"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-signal/15 px-1.5 py-0.2 text-[10px] font-bold text-signal border border-signal/30">
                        {log.verb}
                      </span>
                      <span className="text-foreground font-semibold">{log.sourceNode}</span>
                    </div>
                    <span className="text-muted-foreground text-[10px]">{log.timestamp}</span>
                  </div>
                  <pre className="text-[11px] text-muted-foreground overflow-auto p-1 max-h-24">
                    {JSON.stringify(log.payload, null, 2)}
                  </pre>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Tab 6: DesignIR Source */}
      {activeTab === "designir" && (
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <div className="space-y-0.5">
              <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Code2 className="h-4 w-4 text-primary" />
                <span>Pure DesignIR Document Source (Editable JSON)</span>
              </h3>
              <p className="text-xs text-muted-foreground">
                Deterministic compiler consumes this DesignIR document to synthesize the ViewSpec
                program on every keystroke.
              </p>
            </div>
            <span className="font-mono text-[10px] text-muted-foreground">Compiler Input</span>
          </div>

          <textarea
            rows={22}
            value={irText}
            onChange={(e) => setIrText(e.target.value)}
            className="w-full rounded-lg border border-border bg-background p-4 font-mono text-xs text-foreground focus:border-primary outline-none custom-scrollbar leading-relaxed"
          />
        </div>
      )}

      {/* Tab 7: Interaction Context & Operator Persona Inspector */}
      {activeTab === "context" && (
        <ContextTab
          contextStore={contextStore}
          operator={operator}
          viewSpec={viewSpec}
          multiViewSpec={multiViewSpec}
          stores={storesRef.current}
          onOpenHelpDrawer={handleOpenDrawerHelp}
          onOpenHelpVideo={handleOpenVideoHelp}
        />
      )}

      {/* Ephemeral Help / Manual Mode Overlays */}
      <HelpDrawer
        open={helpDrawerOpen}
        onClose={() => {
          setHelpDrawerOpen(false);
          operator.closeHelp();
        }}
        documentation={currentHelpEntry}
        onLaunchVideo={() => {
          setHelpDrawerOpen(false);
          setHelpVideoOpen(true);
          operator.openHelp("video", currentHelpEntry.capabilityId);
        }}
      />

      <HelpVideoOverlay
        open={helpVideoOpen}
        onClose={() => {
          setHelpVideoOpen(false);
          operator.closeHelp();
        }}
        documentation={currentHelpEntry}
        onOpenManualDrawer={() => {
          setHelpVideoOpen(false);
          setHelpDrawerOpen(true);
          operator.openHelp("drawer", currentHelpEntry.capabilityId);
        }}
      />
    </div>
  );
}

/**
 * Reactive Surface Node
 * Subscribes directly to ContractStateStore ensuring all presentation updates
 * originate from the ContractStateStore rather than direct prop mutation.
 * Features operator highlight detection, interaction tracking, and documentation triggers.
 */
function ReactiveSurfaceNode({
  node,
  widgetInstance,
  store,
  storeVersion,
  widgets,
  isHighlighted,
  highlightMessage,
  isActiveWidget,
  onWidgetClick,
  onFocus,
  onBlur,
  onOpenHelp,
  onDispatch,
}: {
  node: LayoutNode;
  widgetInstance: WidgetInstance;
  store?: InMemoryContractStateStore<unknown>;
  storeVersion: number;
  widgets: Widget[];
  isHighlighted?: boolean;
  highlightMessage?: string;
  isActiveWidget?: boolean;
  onWidgetClick?: () => void;
  onFocus?: () => void;
  onBlur?: () => void;
  onOpenHelp?: (mode: "drawer" | "video") => void;
  onDispatch: (verb: string, payload: unknown) => void;
}) {
  const [contractState, setContractState] = React.useState<unknown>(() => store?.get() ?? {});

  React.useEffect(() => {
    if (!store) return;
    const unsubscribe = store.subscribe((nextState) => {
      setContractState(nextState);
    });
    return unsubscribe;
  }, [store, storeVersion]);

  const capabilityId = widgetInstance.widget.contract;
  const capMeta = CAPABILITY_REGISTRY[capabilityId] || CAPABILITY_REGISTRY.EntityCollection;

  // Find matching widget implementation in catalog
  const matchingWidget =
    widgets.find((w) => {
      const idLower = w.id.toLowerCase();
      if (
        capabilityId === "MetricSeries" &&
        (idLower.includes("sparkline") || idLower.includes("gauge"))
      )
        return true;
      if (
        capabilityId === "StatusBoard" &&
        (idLower.includes("status") || idLower.includes("kanban"))
      )
        return true;
      if (
        capabilityId === "Timeline" &&
        (idLower.includes("revision") || idLower.includes("diff") || idLower.includes("timeline"))
      )
        return true;
      if (
        capabilityId === "KeyMetricMatrix" &&
        (idLower.includes("kpi") || idLower.includes("matrix") || idLower.includes("execution"))
      )
        return true;
      if (capabilityId === "WorkQueue" && idLower.includes("execution")) return true;
      if (
        capabilityId === "ConsensusMatrix" &&
        (idLower.includes("consensus") || idLower.includes("deliberation"))
      )
        return true;
      if (
        capabilityId === "EntityCollection" &&
        (idLower.includes("inventory") || idLower.includes("cross-ref"))
      )
        return true;
      return false;
    }) || widgets[0];

  return (
    <div
      onMouseEnter={onFocus}
      onMouseLeave={onBlur}
      onClick={onWidgetClick}
      className={`panel overflow-hidden flex flex-col transition-all duration-300 shadow-sm ${
        isHighlighted
          ? "ring-2 ring-accent border-accent/80 shadow-lg shadow-accent/20 bg-accent/5 animate-pulse"
          : isActiveWidget
            ? "ring-1 ring-primary/60 border-primary/50"
            : "hover:border-primary/40"
      }`}
    >
      {/* Active Highlight Banner */}
      {isHighlighted && highlightMessage && (
        <div className="bg-accent/20 border-b border-accent/40 px-3.5 py-1.5 text-[11px] font-mono text-accent flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-accent animate-spin" />
          <span className="font-semibold">Operator Focus: {highlightMessage}</span>
        </div>
      )}

      {/* Node Meta Strip */}
      <div className="flex items-center justify-between border-b border-border px-3.5 py-2 bg-surface/60">
        <div className="flex items-center gap-2">
          <span className={`live-dot ${isActiveWidget ? "bg-primary" : ""}`} />
          <span className="font-mono text-xs font-semibold text-foreground">{capabilityId}</span>
          <span className="rounded bg-muted px-1.5 py-0.2 font-mono text-[9px] text-muted-foreground uppercase">
            {node.region} · {node.priority || "secondary"}
          </span>
          {isActiveWidget && (
            <span className="rounded bg-primary/20 text-primary px-1.5 py-0.2 font-mono text-[9px] font-bold">
              ACTIVE
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Docs and Manual Mode triggers */}
          {onOpenHelp && (
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenHelp("drawer");
                }}
                title="View Capability Documentation & Schema"
                className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-surface/80 text-[10px] font-mono flex items-center gap-0.5 border border-border/40"
              >
                <span>Docs</span>
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenHelp("video");
                }}
                title="Simulate Walkthrough Video"
                className="p-1 rounded text-accent hover:text-accent-foreground hover:bg-accent/20 text-[10px] font-mono flex items-center gap-0.5 border border-accent/30"
              >
                <Play className="h-2.5 w-2.5" />
                <span>Demo</span>
              </button>
            </div>
          )}

          {matchingWidget && (
            <Link
              to="/widget/$id"
              params={{ id: matchingWidget.id }}
              className="font-mono text-[10px] text-primary hover:underline flex items-center gap-1 ml-1"
            >
              <span>{matchingWidget.name}</span>
              <ExternalLink className="h-2.5 w-2.5" />
            </Link>
          )}
        </div>
      </div>

      {/* Rendered Widget Sandbox bound to ContractStateStore state */}
      <div className="p-4 flex-1 flex items-center justify-center bg-background/50 overflow-auto">
        {matchingWidget ? (
          <div
            onClick={() =>
              onDispatch("inspect", {
                widgetId: widgetInstance.id,
                capability: capabilityId,
                contractState,
              })
            }
            className="w-full cursor-pointer"
          >
            <WidgetSandbox
              widget={matchingWidget}
              props={contractState as Record<string, unknown>}
              onLog={(l) => onDispatch("log", { line: l })}
              className="w-full"
            />
          </div>
        ) : (
          <div className="p-6 text-center text-xs font-mono text-muted-foreground">
            {capabilityId} (No widget bound)
          </div>
        )}
      </div>

      {/* Interactive Verbs Bar */}
      <div className="border-t border-border/40 bg-surface/40 px-3.5 py-2 flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[9px] text-muted-foreground uppercase">Emits:</span>
        {capMeta.outputEvents.map((evt) => (
          <button
            key={evt.name}
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDispatch(evt.name, {
                widgetId: widgetInstance.id,
                capability: capabilityId,
                timestamp: Date.now(),
              });
            }}
            className="inline-flex items-center gap-1 rounded border border-signal/40 bg-signal/10 px-2 py-0.5 font-mono text-[10px] text-signal hover:bg-signal/20 transition-colors"
          >
            <Zap className="h-2.5 w-2.5" />
            <span>{evt.name}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
