import * as React from "react";
import {
  Sparkles,
  Bot,
  Terminal,
  MousePointerClick,
  Focus,
  BookOpen,
  Play,
  RotateCcw,
  ArrowRight,
  Layers,
  Database,
  Crosshair,
} from "lucide-react";
import { InteractionContext, InteractionContextStoreAPI } from "@/core/runtime/interactionContext";
import { OperatorPersonaAPI } from "@/core/runtime/operatorPersona";
import { toast } from "sonner";

interface ContextTabProps {
  contextStore: InteractionContextStoreAPI;
  operator: OperatorPersonaAPI;
  onOpenDrawer: (targetWidgetId?: string) => void;
  onOpenVideo: (targetWidgetId?: string) => void;
}

export const ContextTab: React.FC<ContextTabProps> = ({
  contextStore,
  operator,
  onOpenDrawer,
  onOpenVideo,
}) => {
  const [context, setContext] = React.useState<InteractionContext>(contextStore.get());
  const [simWidgetId, setSimWidgetId] = React.useState("widget-pipeline");
  const [simRoleId, setSimRoleId] = React.useState("pipeline");
  const [simEntityId, setSimEntityId] = React.useState("ENT-104");

  React.useEffect(() => {
    const unsubscribe = contextStore.subscribe((ctx) => {
      setContext(ctx);
    });
    return () => unsubscribe();
  }, [contextStore]);

  const handleSimulateClick = () => {
    contextStore.onWidgetClick(simWidgetId, simRoleId);
    toast.success(`Dispatched onWidgetClick(${simWidgetId}, ${simRoleId})`);
  };

  const handleSimulateFocus = () => {
    contextStore.onWidgetFocus(`ctrl-${simWidgetId}`);
    toast.success(`Dispatched onWidgetFocus(ctrl-${simWidgetId})`);
  };

  const handleSimulateBlur = () => {
    contextStore.onWidgetBlur(`ctrl-${simWidgetId}`);
    toast.success(`Dispatched onWidgetBlur(ctrl-${simWidgetId})`);
  };

  const handleSimulateRowSelect = () => {
    contextStore.onRowSelect(simWidgetId, simEntityId, 3);
    toast.success(`Dispatched onRowSelect(${simWidgetId}, ${simEntityId}, 3)`);
  };

  const handleResetContext = () => {
    contextStore.reset();
    toast.info("Reset ephemeral InteractionContext");
  };

  return (
    <div className="panel p-5 space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/40 pb-3">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">
              InteractionContextStore & Operator Telemetry
            </h3>
          </div>
          <p className="text-xs text-muted-foreground">
            Canonical Invariant: Ephemeral, runtime-only operator context model tracking active
            surfaces, workflow steps, focus, selections, and help overlays. Never part of compiler
            outputs.
          </p>
        </div>

        <button
          type="button"
          onClick={handleResetContext}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-surface hover:bg-surface/80 border border-border font-mono text-xs text-foreground transition-colors"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          <span>Reset Context</span>
        </button>
      </div>

      {/* Snapshot Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Core Operator Context */}
        <div className="p-4 rounded-lg border border-border/60 bg-background/60 space-y-3 font-mono text-xs">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <span className="font-bold text-foreground flex items-center gap-1.5">
              <Sparkles className="h-4 w-4 text-primary" />
              <span>Active Surface & Workflow</span>
            </span>
            <span className="live-dot" />
          </div>

          <div className="space-y-1.5 text-[11px]">
            <div>
              <span className="text-muted-foreground">activeSurfaceId:</span>{" "}
              <strong className="text-primary">{context.activeSurfaceId}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">activeWorkflowId:</span>{" "}
              <strong className="text-accent">{context.activeWorkflowId || "undefined"}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">activeWorkflowStepId:</span>{" "}
              <strong className="text-signal">{context.activeWorkflowStepId || "undefined"}</strong>
            </div>
          </div>
        </div>

        {/* Focus & Selection Context */}
        <div className="p-4 rounded-lg border border-border/60 bg-background/60 space-y-3 font-mono text-xs">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <span className="font-bold text-foreground flex items-center gap-1.5">
              <MousePointerClick className="h-4 w-4 text-accent" />
              <span>Widget & Entity State</span>
            </span>
            <span className="text-[10px] text-muted-foreground">DYNAMIC</span>
          </div>

          <div className="space-y-1.5 text-[11px]">
            <div>
              <span className="text-muted-foreground">activeWidgetId:</span>{" "}
              <strong className="text-foreground">{context.activeWidgetId || "none"}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">activeRoleId:</span>{" "}
              <strong className="text-foreground">{context.activeRoleId || "none"}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">focusedControlId:</span>{" "}
              <strong className="text-foreground">{context.focusedControlId || "none"}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">selectedEntity:</span>{" "}
              <strong className="text-signal">
                {context.selectedEntity
                  ? `${context.selectedEntity.entityId} in ${context.selectedEntity.widgetId}`
                  : "none"}
              </strong>
            </div>
          </div>
        </div>

        {/* Help / Manual Mode */}
        <div className="p-4 rounded-lg border border-border/60 bg-background/60 space-y-3 font-mono text-xs">
          <div className="flex items-center justify-between border-b border-border/40 pb-2">
            <span className="font-bold text-foreground flex items-center gap-1.5">
              <BookOpen className="h-4 w-4 text-signal" />
              <span>Manual / Help Mode</span>
            </span>
            <span
              className={`rounded px-1.5 py-0.2 text-[9px] font-bold ${
                context.helpMode?.active
                  ? "bg-signal/20 text-signal border border-signal/40"
                  : "bg-surface text-muted-foreground"
              }`}
            >
              {context.helpMode?.active ? "ACTIVE" : "INACTIVE"}
            </span>
          </div>

          <div className="space-y-1.5 text-[11px]">
            <div>
              <span className="text-muted-foreground">helpMode.active:</span>{" "}
              <strong>{String(context.helpMode?.active || false)}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">helpMode.level:</span>{" "}
              <strong className="text-primary">{context.helpMode?.level || "none"}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">targetWidgetId:</span>{" "}
              <strong>{context.helpMode?.targetWidgetId || "none"}</strong>
            </div>
          </div>
        </div>
      </div>

      {/* Interactive Simulation & Test Dispatcher */}
      <div className="p-4 rounded-lg border border-border bg-surface/60 space-y-4">
        <div className="flex items-center gap-2 font-mono text-xs font-bold text-foreground">
          <Terminal className="h-4 w-4 text-primary" />
          <span>Simulate Contextual Runtime Event Hooks</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="space-y-1">
            <label className="text-[10px] font-mono text-muted-foreground">Target Widget ID</label>
            <input
              type="text"
              value={simWidgetId}
              onChange={(e) => setSimWidgetId(e.target.value)}
              className="w-full rounded border border-border bg-background px-2.5 py-1.5 font-mono text-xs text-foreground"
            />
          </div>

          <div className="space-y-1">
            <label className="text-[10px] font-mono text-muted-foreground">Target Role ID</label>
            <input
              type="text"
              value={simRoleId}
              onChange={(e) => setSimRoleId(e.target.value)}
              className="w-full rounded border border-border bg-background px-2.5 py-1.5 font-mono text-xs text-foreground"
            />
          </div>

          <div className="space-y-1">
            <label className="text-[10px] font-mono text-muted-foreground">Entity ID</label>
            <input
              type="text"
              value={simEntityId}
              onChange={(e) => setSimEntityId(e.target.value)}
              className="w-full rounded border border-border bg-background px-2.5 py-1.5 font-mono text-xs text-foreground"
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-2 pt-2 border-t border-border/40 font-mono text-xs">
          <button
            type="button"
            onClick={handleSimulateClick}
            className="px-3 py-1.5 rounded bg-primary/15 border border-primary/40 text-primary hover:bg-primary/25 transition-colors font-semibold"
          >
            ➔ onWidgetClick()
          </button>
          <button
            type="button"
            onClick={handleSimulateFocus}
            className="px-3 py-1.5 rounded bg-surface border border-border text-foreground hover:bg-surface/80 transition-colors"
          >
            ➔ onWidgetFocus()
          </button>
          <button
            type="button"
            onClick={handleSimulateBlur}
            className="px-3 py-1.5 rounded bg-surface border border-border text-foreground hover:bg-surface/80 transition-colors"
          >
            ➔ onWidgetBlur()
          </button>
          <button
            type="button"
            onClick={handleSimulateRowSelect}
            className="px-3 py-1.5 rounded bg-signal/15 border border-signal/40 text-signal hover:bg-signal/25 transition-colors font-semibold"
          >
            ➔ onRowSelect()
          </button>
          <button
            type="button"
            onClick={() => onOpenDrawer(simWidgetId)}
            className="px-3 py-1.5 rounded bg-accent/15 border border-accent/40 text-accent hover:bg-accent/25 transition-colors font-semibold"
          >
            ➔ onHelpMode('drawer')
          </button>
          <button
            type="button"
            onClick={() => onOpenVideo(simWidgetId)}
            className="px-3 py-1.5 rounded bg-signal/15 border border-signal/40 text-signal hover:bg-signal/25 transition-colors font-semibold"
          >
            ➔ onHelpMode('video')
          </button>
        </div>
      </div>

      {/* Raw JSON Snapshot */}
      <div className="space-y-2">
        <div className="label-mono text-[11px] text-muted-foreground">
          InteractionContextStore.get() Live JSON Snapshot:
        </div>
        <pre className="p-4 rounded-lg border border-border/60 bg-background/80 font-mono text-xs text-foreground max-h-60 overflow-auto custom-scrollbar">
          {JSON.stringify(context, null, 2)}
        </pre>
      </div>
    </div>
  );
};
