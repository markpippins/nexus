import * as React from "react";
import {
  Bot,
  Sparkles,
  BookOpen,
  Play,
  ArrowRight,
  ArrowLeft,
  Crosshair,
  HelpCircle,
  Terminal,
  Send,
  Eye,
  SlidersHorizontal,
  XCircle,
  Lightbulb,
} from "lucide-react";
import { OperatorPersonaAPI, OperatorNarration } from "@/core/runtime/operatorPersona";
import { InteractionContext } from "@/core/runtime/interactionContext";

interface OperatorPanelProps {
  operator: OperatorPersonaAPI;
  context: InteractionContext;
  onOpenDrawer: () => void;
  onOpenVideo: () => void;
}

export const OperatorPanel: React.FC<OperatorPanelProps> = ({
  operator,
  context,
  onOpenDrawer,
  onOpenVideo,
}) => {
  const [narrations, setNarrations] = React.useState<OperatorNarration[]>([]);
  const [highlights, setHighlights] = React.useState(operator.getHighlights());
  const [promptInput, setPromptInput] = React.useState("");

  React.useEffect(() => {
    setNarrations(operator.getNarrations());
    setHighlights(operator.getHighlights());

    const unsubscribe = operator.subscribe(() => {
      setNarrations(operator.getNarrations());
      setHighlights(operator.getHighlights());
    });

    return () => unsubscribe();
  }, [operator]);

  const handleAskOperator = (e: React.FormEvent) => {
    e.preventDefault();
    if (!promptInput.trim()) return;

    const query = promptInput.trim();
    setPromptInput("");

    // Simulate Operator contextual intelligence response
    operator.narrate(`Operator Query: "${query}"`, "info");

    setTimeout(() => {
      if (query.toLowerCase().includes("help") || query.toLowerCase().includes("manual")) {
        operator.openHelp("drawer", context.activeWidgetId);
      } else if (query.toLowerCase().includes("video")) {
        operator.openHelp("video", context.activeWidgetId);
      } else if (query.toLowerCase().includes("workflow") || query.toLowerCase().includes("next")) {
        operator.nextStep();
      } else if (query.toLowerCase().includes("highlight") && context.activeWidgetId) {
        operator.highlightWidget(context.activeWidgetId);
        operator.narrate(`Highlighted active widget "${context.activeWidgetId}".`, "guidance");
      } else {
        operator.narrate(
          `Context Analysis: You are on surface "${context.activeSurfaceId}". ${
            context.activeWidgetId
              ? `Currently inspecting ${context.activeWidgetId}.`
              : "No specific widget is currently focused."
          } ${context.selectedEntity ? `Selected entity: ${context.selectedEntity.entityId}.` : ""}`,
          "guidance",
        );
      }
    }, 300);
  };

  return (
    <div className="panel p-5 space-y-4 border border-primary/30 bg-surface/90 shadow-md">
      {/* Operator Presence Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 pb-3">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-lg bg-primary/15 text-primary border border-primary/30">
            <Bot className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="live-dot" />
              <h3 className="font-bold text-foreground text-sm tracking-tight">
                Operator Persona // Contextual UI Co-Pilot
              </h3>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Observes live InteractionContext & ContractStateStore to provide continuous narration
              and guided walkthroughs.
            </p>
          </div>
        </div>

        {/* Quick Help & Annotation Controls */}
        <div className="flex items-center gap-1.5 font-mono text-xs">
          <button
            type="button"
            onClick={onOpenDrawer}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-surface hover:bg-surface/80 border border-border text-foreground hover:text-primary transition-colors"
          >
            <BookOpen className="h-3.5 w-3.5 text-primary" />
            <span>Manual Drawer</span>
          </button>

          <button
            type="button"
            onClick={onOpenVideo}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-surface hover:bg-surface/80 border border-border text-foreground hover:text-signal transition-colors"
          >
            <Play className="h-3.5 w-3.5 text-signal fill-signal" />
            <span>Video Guide</span>
          </button>

          {context.activeWidgetId && (
            <button
              type="button"
              onClick={() => {
                if (highlights.highlightedWidgetId === context.activeWidgetId) {
                  operator.clearHighlights();
                } else {
                  operator.highlightWidget(context.activeWidgetId!);
                }
              }}
              className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md border text-[11px] font-mono transition-colors ${
                highlights.highlightedWidgetId === context.activeWidgetId
                  ? "bg-accent/20 border-accent text-accent font-bold"
                  : "bg-surface hover:bg-surface/80 border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              <Crosshair className="h-3.5 w-3.5 text-accent" />
              <span>
                {highlights.highlightedWidgetId === context.activeWidgetId
                  ? "Clear Highlight"
                  : "Highlight"}
              </span>
            </button>
          )}
        </div>
      </div>

      {/* Real-Time Contextual Telemetry Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-[11px]">
        <div className="p-2 rounded border border-border/60 bg-background/50 space-y-0.5">
          <div className="text-[10px] text-muted-foreground uppercase">Active Surface</div>
          <div className="font-bold text-foreground truncate">
            {context.activeSurfaceId || "main"}
          </div>
        </div>

        <div className="p-2 rounded border border-border/60 bg-background/50 space-y-0.5">
          <div className="text-[10px] text-muted-foreground uppercase">Workflow / Step</div>
          <div className="font-bold text-primary truncate">
            {context.activeWorkflowId
              ? `${context.activeWorkflowId} (${context.activeWorkflowStepId || "step-1"})`
              : "None (Ambient)"}
          </div>
        </div>

        <div className="p-2 rounded border border-border/60 bg-background/50 space-y-0.5">
          <div className="text-[10px] text-muted-foreground uppercase">Active Widget / Role</div>
          <div className="font-bold text-accent truncate">
            {context.activeWidgetId || "None Focused"}
            {context.activeRoleId ? ` · [${context.activeRoleId}]` : ""}
          </div>
        </div>

        <div className="p-2 rounded border border-border/60 bg-background/50 space-y-0.5">
          <div className="text-[10px] text-muted-foreground uppercase">Selected Entity</div>
          <div className="font-bold text-signal truncate">
            {context.selectedEntity
              ? `${context.selectedEntity.entityId} (${context.selectedEntity.widgetId})`
              : "None Selected"}
          </div>
        </div>
      </div>

      {/* Workflow Stepping Bar (if in workflow) */}
      <div className="p-3 rounded-lg border border-border/70 bg-surface/50 flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-mono text-muted-foreground text-[11px]">
            Workflow Orchestrator:
          </span>
          {context.activeWorkflowId ? (
            <span className="font-bold text-primary font-mono text-xs">
              {context.activeWorkflowId} ➔ Step: {context.activeWorkflowStepId || "1"}
            </span>
          ) : (
            <span className="text-muted-foreground italic text-xs">No active workflow running</span>
          )}
        </div>

        <div className="flex items-center gap-1.5 font-mono text-xs">
          {!context.activeWorkflowId ? (
            <button
              type="button"
              onClick={() => operator.startWorkflow("triage-incident")}
              className="px-3 py-1 rounded bg-primary text-primary-foreground font-semibold text-xs hover:bg-primary/90 transition-all shadow-xs"
            >
              Start Incident Workflow
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => operator.previousStep()}
                className="flex items-center gap-1 px-2.5 py-1 rounded bg-surface border border-border text-foreground hover:bg-surface/80 text-xs"
              >
                <ArrowLeft className="h-3 w-3" />
                <span>Prev Step</span>
              </button>
              <button
                type="button"
                onClick={() => operator.nextStep()}
                className="flex items-center gap-1 px-2.5 py-1 rounded bg-primary text-primary-foreground font-semibold hover:bg-primary/90 text-xs shadow-xs"
              >
                <span>Next Step</span>
                <ArrowRight className="h-3 w-3" />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Live Operator Narration Stream */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-[11px] font-mono text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-primary" />
            <span>Operator Narration & Context Stream ({narrations.length})</span>
          </span>
          {narrations.length > 0 && (
            <button
              type="button"
              onClick={() => operator.clearNarrations()}
              className="hover:text-foreground text-[10px]"
            >
              Clear Log
            </button>
          )}
        </div>

        <div className="max-h-44 overflow-y-auto rounded-lg border border-border/60 bg-background/60 p-3 space-y-2 custom-scrollbar font-mono text-xs">
          {narrations.length === 0 ? (
            <div className="text-muted-foreground text-center py-4 text-xs italic">
              Operator is observing the environment. Click widgets or navigate surfaces to trigger
              live contextual narration.
            </div>
          ) : (
            narrations.map((n) => (
              <div
                key={n.id}
                className="p-2 rounded bg-surface/70 border border-border/50 flex items-start justify-between gap-3 text-xs"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-[9px] font-bold px-1.5 py-0.2 rounded border ${
                        n.category === "workflow"
                          ? "bg-primary/15 border-primary/40 text-primary"
                          : n.category === "guidance"
                            ? "bg-accent/15 border-accent/40 text-accent"
                            : n.category === "alert"
                              ? "bg-destructive/15 border-destructive/40 text-destructive"
                              : "bg-surface border-border text-muted-foreground"
                      }`}
                    >
                      {n.category.toUpperCase()}
                    </span>
                    <span className="text-foreground font-sans text-xs">{n.message}</span>
                  </div>

                  {n.suggestedAction && (
                    <button
                      type="button"
                      onClick={n.suggestedAction.action}
                      className="text-[10px] text-primary hover:underline flex items-center gap-1 font-sans"
                    >
                      <span>➔ {n.suggestedAction.label}</span>
                    </button>
                  )}
                </div>

                <span className="text-[10px] text-muted-foreground shrink-0">{n.timestamp}</span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Ask Operator Prompt Form */}
      <form onSubmit={handleAskOperator} className="flex items-center gap-2">
        <input
          type="text"
          value={promptInput}
          onChange={(e) => setPromptInput(e.target.value)}
          placeholder="Ask Operator (e.g. 'Explain this widget', 'Open manual', 'Next step', 'Highlight')..."
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-xs font-mono text-foreground focus:border-primary outline-none"
        />
        <button
          type="submit"
          className="px-3 py-2 rounded-md bg-primary text-primary-foreground font-mono text-xs font-semibold hover:bg-primary/90 transition-all flex items-center gap-1 shadow-xs"
        >
          <Send className="h-3 w-3" />
          <span>Ask</span>
        </button>
      </form>
    </div>
  );
};
