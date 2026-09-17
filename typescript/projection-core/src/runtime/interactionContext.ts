/**
 * InteractionContextStore — Canonical Runtime Implementation
 *
 * Tracks live, ephemeral operator context (active surface, workflow, focus,
 * selection, clicks, and manual/help mode).
 *
 * Invariant: Never part of compiler, ViewSpec, or DesignIR. Lives strictly in the runtime VM.
 */

export interface InteractionContext {
  activeSurfaceId: string;
  activeWorkflowId?: string;
  activeWorkflowStepId?: string;

  activeWidgetId?: string;
  activeRoleId?: string;

  focusedControlId?: string;
  lastClickedWidgetId?: string;
  lastClickedRoleId?: string;

  selectedEntity?: {
    widgetId: string;
    entityId: string;
    rowIndex?: number;
  };

  lastEvent?: {
    widgetId: string;
    eventType: string;
    payload?: unknown;
  };

  helpMode?: {
    active: boolean;
    level: "operator" | "manual" | "video" | "drawer";
    targetWidgetId?: string;
  };
}

export interface InteractionContextEventHooks {
  // Widget-level events
  onWidgetClick(widgetId: string, roleId?: string): void;
  onWidgetFocus(controlId: string): void;
  onWidgetBlur(controlId: string): void;
  onRowSelect(widgetId: string, entityId: string, rowIndex?: number): void;

  // EventBus → InteractionContextStore
  onEventDispatch(widgetId: string, eventType: string, payload?: unknown): void;

  // Navigation & workflow transitions
  onSurfaceNavigate(surfaceId: string): void;
  onWorkflowStepEnter(workflowId: string, stepId: string, focusRoleId?: string): void;

  // Help / Manual Mode
  onHelpMode(level: "operator" | "manual" | "video" | "drawer", targetWidgetId?: string): void;
  onHelpModeExit(): void;
}

export interface InteractionContextStoreAPI extends InteractionContextEventHooks {
  get(): InteractionContext;
  subscribe(listener: (ctx: InteractionContext) => void): () => void;
  update(partial: Partial<InteractionContext>): void;
  reset(): void;
}

export class DefaultInteractionContextStore implements InteractionContextStoreAPI {
  private context: InteractionContext;
  private listeners: Set<(ctx: InteractionContext) => void> = new Set();

  constructor(initialSurfaceId: string = "main") {
    this.context = {
      activeSurfaceId: initialSurfaceId,
    };
  }

  get(): InteractionContext {
    return { ...this.context };
  }

  subscribe(listener: (ctx: InteractionContext) => void): () => void {
    this.listeners.add(listener);
    listener(this.get());
    return () => {
      this.listeners.delete(listener);
    };
  }

  update(partial: Partial<InteractionContext>): void {
    this.context = {
      ...this.context,
      ...partial,
    };
    this.notify();
  }

  reset(): void {
    this.context = {
      activeSurfaceId: this.context.activeSurfaceId,
    };
    this.notify();
  }

  onWidgetClick(widgetId: string, roleId?: string): void {
    this.update({
      lastClickedWidgetId: widgetId,
      lastClickedRoleId: roleId,
      activeWidgetId: widgetId,
      activeRoleId: roleId,
    });
  }

  onWidgetFocus(controlId: string): void {
    this.update({
      focusedControlId: controlId,
    });
  }

  onWidgetBlur(controlId: string): void {
    if (this.context.focusedControlId === controlId) {
      this.update({
        focusedControlId: undefined,
      });
    }
  }

  onRowSelect(widgetId: string, entityId: string, rowIndex?: number): void {
    this.update({
      selectedEntity: {
        widgetId,
        entityId,
        rowIndex,
      },
      activeWidgetId: widgetId,
    });
  }

  onEventDispatch(widgetId: string, eventType: string, payload?: unknown): void {
    this.update({
      lastEvent: {
        widgetId,
        eventType,
        payload,
      },
    });
  }

  onSurfaceNavigate(surfaceId: string): void {
    this.update({
      activeSurfaceId: surfaceId,
      activeWidgetId: undefined,
      activeRoleId: undefined,
      focusedControlId: undefined,
      selectedEntity: undefined,
    });
  }

  onWorkflowStepEnter(workflowId: string, stepId: string, focusRoleId?: string): void {
    this.update({
      activeWorkflowId: workflowId,
      activeWorkflowStepId: stepId,
      activeRoleId: focusRoleId,
      activeWidgetId: focusRoleId ? `widget-${focusRoleId}` : this.context.activeWidgetId,
    });
  }

  onHelpMode(level: "operator" | "manual" | "video" | "drawer", targetWidgetId?: string): void {
    this.update({
      helpMode: {
        active: true,
        level,
        targetWidgetId,
      },
    });
  }

  onHelpModeExit(): void {
    this.update({
      helpMode: undefined,
    });
  }

  private notify(): void {
    const snapshot = this.get();
    for (const listener of this.listeners) {
      try {
        listener(snapshot);
      } catch (err) {
        console.error("InteractionContext listener error:", err);
      }
    }
  }
}
