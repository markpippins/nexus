/**
 * Operator Persona Runtime Implementation
 *
 * The Operator is a persistent, runtime-only persona that interprets live context,
 * narrates what the user is seeing, guides workflows, and triggers manual/help mode overlays.
 *
 * Invariant: Never mutates ViewSpec, DesignIR, or compiler output. Pure reader and guided actor.
 */

import { InteractionContext, InteractionContextStoreAPI } from "./interactionContext";
import { ViewSpec, MultiSurfaceViewSpec } from "../types/viewSpec";
import { ContractStateStore } from "./contractState";

export interface OperatorNarration {
  id: string;
  timestamp: string;
  message: string;
  category: "info" | "guidance" | "workflow" | "alert";
  relatedWidgetId?: string;
  suggestedAction?: {
    label: string;
    action: () => void;
  };
}

export interface OperatorHighlightState {
  highlightedWidgetId?: string;
  highlightedRoleId?: string;
}

export interface OperatorPersonaAPI {
  // Read context
  getContext(): InteractionContext;
  getState(widgetId: string): unknown;
  getViewSpec(): ViewSpec | MultiSurfaceViewSpec | undefined;

  // Narration
  narrate(
    message: string,
    category?: "info" | "guidance" | "workflow" | "alert",
    relatedWidgetId?: string,
    suggestedAction?: { label: string; action: () => void },
  ): void;
  getNarrations(): OperatorNarration[];
  clearNarrations(): void;

  // Workflow control
  startWorkflow(workflowId: string): void;
  nextStep(): void;
  previousStep(): void;
  goToStep(stepId: string): void;

  // Help / Manual mode
  openHelp(level: "operator" | "manual" | "video" | "drawer", targetWidgetId?: string): void;
  closeHelp(): void;

  // Surface annotation
  highlightWidget(widgetId: string): void;
  highlightRole(roleId: string): void;
  clearHighlights(): void;
  getHighlights(): OperatorHighlightState;

  // Subscriptions
  subscribe(listener: () => void): () => void;
}

export class DefaultOperatorPersona implements OperatorPersonaAPI {
  private contextStore: InteractionContextStoreAPI;
  private contractStores: Map<string, ContractStateStore>;
  private viewSpecProvider: () => ViewSpec | MultiSurfaceViewSpec | undefined;
  private narrations: OperatorNarration[] = [];
  private highlights: OperatorHighlightState = {};
  private listeners: Set<() => void> = new Set();
  private unsubscribeContext?: () => void;

  constructor(
    contextStore: InteractionContextStoreAPI,
    contractStores: Map<string, ContractStateStore>,
    viewSpecProvider: () => ViewSpec | MultiSurfaceViewSpec | undefined,
  ) {
    this.contextStore = contextStore;
    this.contractStores = contractStores;
    this.viewSpecProvider = viewSpecProvider;

    // Reactively listen to context changes to provide automated Operator co-pilot narration
    this.unsubscribeContext = this.contextStore.subscribe((ctx) => {
      this.handleContextChange(ctx);
    });
  }

  getContext(): InteractionContext {
    return this.contextStore.get();
  }

  getState(widgetId: string): unknown {
    const store = this.contractStores.get(widgetId);
    return store ? store.get() : undefined;
  }

  getViewSpec(): ViewSpec | MultiSurfaceViewSpec | undefined {
    return this.viewSpecProvider();
  }

  narrate(
    message: string,
    category: "info" | "guidance" | "workflow" | "alert" = "info",
    relatedWidgetId?: string,
    suggestedAction?: { label: string; action: () => void },
  ): void {
    const entry: OperatorNarration = {
      id: `narrate-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
      message,
      category,
      relatedWidgetId,
      suggestedAction,
    };
    this.narrations = [entry, ...this.narrations].slice(0, 50);
    this.notify();
  }

  getNarrations(): OperatorNarration[] {
    return [...this.narrations];
  }

  clearNarrations(): void {
    this.narrations = [];
    this.notify();
  }

  startWorkflow(workflowId: string): void {
    const spec = this.getViewSpec();
    let firstStepId = "step-1";
    let focusRoleId: string | undefined;

    if (spec && "workflows" in spec && spec.workflows?.[workflowId]) {
      const wf = spec.workflows[workflowId];
      if (wf.steps.length > 0) {
        firstStepId = wf.steps[0].id;
        focusRoleId = wf.steps[0].focusRoleId;
      }
    }

    this.contextStore.onWorkflowStepEnter(workflowId, firstStepId, focusRoleId);
    this.narrate(
      `Starting workflow "${workflowId}" at step "${firstStepId}".`,
      "workflow",
      focusRoleId ? `widget-${focusRoleId}` : undefined,
    );
  }

  nextStep(): void {
    const ctx = this.getContext();
    if (!ctx.activeWorkflowId || !ctx.activeWorkflowStepId) {
      this.narrate("No active workflow to advance.", "guidance");
      return;
    }

    const spec = this.getViewSpec();
    if (spec && "workflows" in spec && spec.workflows?.[ctx.activeWorkflowId]) {
      const wf = spec.workflows[ctx.activeWorkflowId];
      const currentIndex = wf.steps.findIndex((s) => s.id === ctx.activeWorkflowStepId);
      if (currentIndex >= 0 && currentIndex < wf.steps.length - 1) {
        const next = wf.steps[currentIndex + 1];
        this.contextStore.onWorkflowStepEnter(ctx.activeWorkflowId, next.id, next.focusRoleId);
        this.narrate(`Advanced to step ${currentIndex + 2}: ${next.name || next.id}.`, "workflow");
        return;
      }
    }

    // Generic advance fallback
    const stepNum = parseInt(ctx.activeWorkflowStepId.replace(/\D/g, "") || "1", 10);
    const nextStepId = `step-${stepNum + 1}`;
    this.contextStore.onWorkflowStepEnter(ctx.activeWorkflowId, nextStepId);
    this.narrate(`Advanced to step "${nextStepId}".`, "workflow");
  }

  previousStep(): void {
    const ctx = this.getContext();
    if (!ctx.activeWorkflowId || !ctx.activeWorkflowStepId) return;

    const spec = this.getViewSpec();
    if (spec && "workflows" in spec && spec.workflows?.[ctx.activeWorkflowId]) {
      const wf = spec.workflows[ctx.activeWorkflowId];
      const currentIndex = wf.steps.findIndex((s) => s.id === ctx.activeWorkflowStepId);
      if (currentIndex > 0) {
        const prev = wf.steps[currentIndex - 1];
        this.contextStore.onWorkflowStepEnter(ctx.activeWorkflowId, prev.id, prev.focusRoleId);
        this.narrate(`Returned to step ${currentIndex}: ${prev.name || prev.id}.`, "workflow");
        return;
      }
    }

    const stepNum = parseInt(ctx.activeWorkflowStepId.replace(/\D/g, "") || "2", 10);
    const prevStepId = `step-${Math.max(1, stepNum - 1)}`;
    this.contextStore.onWorkflowStepEnter(ctx.activeWorkflowId, prevStepId);
    this.narrate(`Returned to step "${prevStepId}".`, "workflow");
  }

  goToStep(stepId: string): void {
    const ctx = this.getContext();
    if (!ctx.activeWorkflowId) return;
    this.contextStore.onWorkflowStepEnter(ctx.activeWorkflowId, stepId);
    this.narrate(`Jumped to step "${stepId}".`, "workflow");
  }

  openHelp(level: "operator" | "manual" | "video" | "drawer", targetWidgetId?: string): void {
    const widgetId = targetWidgetId || this.getContext().activeWidgetId;
    this.contextStore.onHelpMode(level, widgetId);
    this.narrate(
      `Opened ${level} help for ${widgetId || "current surface"}.`,
      "guidance",
      widgetId,
    );
  }

  closeHelp(): void {
    this.contextStore.onHelpModeExit();
    this.narrate("Closed contextual help.", "info");
  }

  highlightWidget(widgetId: string): void {
    this.highlights = { highlightedWidgetId: widgetId };
    this.notify();
  }

  highlightRole(roleId: string): void {
    this.highlights = { highlightedRoleId: roleId };
    this.notify();
  }

  clearHighlights(): void {
    this.highlights = {};
    this.notify();
  }

  getHighlights(): OperatorHighlightState {
    return { ...this.highlights };
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  dispose(): void {
    if (this.unsubscribeContext) {
      this.unsubscribeContext();
    }
  }

  private handleContextChange(ctx: InteractionContext): void {
    // When lastClickedWidgetId changes, offer contextual narration
    if (ctx.lastClickedWidgetId) {
      const widget = ctx.lastClickedWidgetId;
      const role = ctx.lastClickedRoleId;
      // Provide intelligent narration if not already logged
      const lastMsg = this.narrations[0]?.message || "";
      const expected = `Interacted with ${widget}${role ? ` (${role})` : ""}.`;
      if (!lastMsg.includes(widget)) {
        this.narrate(expected, "info", widget, {
          label: "View Manual Docs",
          action: () => this.openHelp("drawer", widget),
        });
      }
    }

    if (ctx.selectedEntity) {
      const ent = ctx.selectedEntity;
      this.narrate(
        `Selected entity "${ent.entityId}" in ${ent.widgetId}${ent.rowIndex !== undefined ? ` (Row #${ent.rowIndex + 1})` : ""}.`,
        "info",
        ent.widgetId,
      );
    }
  }

  private notify(): void {
    for (const listener of this.listeners) {
      try {
        listener();
      } catch (err) {
        console.error("Operator listener error:", err);
      }
    }
  }
}
