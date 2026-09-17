import { RuntimeView } from "./types";
import { ViewSpecAction } from "../types/viewSpec";

export interface ActionInterpreter {
  execute(action: ViewSpecAction, runtime: RuntimeView, eventPayload?: unknown): Promise<void>;
  registerHandler(type: string, handler: ActionHandler): void;
}

export type ActionHandler = (
  action: ViewSpecAction,
  runtime: RuntimeView,
  eventPayload?: unknown,
) => Promise<void>;

/**
 * ActionInterpreter
 *
 * Canonical Invariant: Route all event-driven state changes strictly through
 * ContractStateStore or runtime EventBus, never mutating AST nodes directly.
 */
export class DefaultActionInterpreter implements ActionInterpreter {
  private customHandlers: Map<string, ActionHandler> = new Map();

  constructor() {
    this.registerHandler("navigate", this.handleNavigate.bind(this));
    this.registerHandler("inspect", this.handleInspect.bind(this));
    this.registerHandler("drilldown", this.handleDrilldown.bind(this));
    this.registerHandler("filter", this.handleFilter.bind(this));
    this.registerHandler("sort", this.handleSort.bind(this));
    this.registerHandler("acknowledge", this.handleAcknowledge.bind(this));
    this.registerHandler("dismiss", this.handleDismiss.bind(this));
    this.registerHandler("compare", this.handleCompare.bind(this));
    this.registerHandler("select", this.handleSelect.bind(this));
  }

  registerHandler(type: string, handler: ActionHandler): void {
    this.customHandlers.set(type, handler);
  }

  async execute(
    action: ViewSpecAction,
    runtime: RuntimeView,
    eventPayload?: unknown,
  ): Promise<void> {
    const handler = this.customHandlers.get(action.type);
    if (!handler) {
      console.warn(`No handler registered for action type: ${action.type}`);
      return;
    }

    try {
      await handler(action, runtime, eventPayload);
    } catch (error) {
      console.error(`Action execution failed (${action.type}):`, error);
      throw error;
    }
  }

  private async handleNavigate(
    action: ViewSpecAction,
    runtime: RuntimeView,
    _eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "navigate") return;

    if (action.target) {
      runtime.interactionContext?.onSurfaceNavigate(action.target);
    }

    runtime.eventBus.emit({
      type: "navigation",
      source: "actionInterpreter",
      payload: { target: action.target },
      timestamp: Date.now(),
    });
  }

  private async handleInspect(
    action: ViewSpecAction,
    runtime: RuntimeView,
    eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "inspect") return;
    const targetWidgetId = action.targetWidgetId;
    if (!targetWidgetId) return;

    runtime.interactionContext?.onWidgetClick(targetWidgetId);

    const store = runtime.contractStores.get(targetWidgetId);
    if (store) {
      const current = (store.get() || {}) as Record<string, unknown>;
      // Route state change through ContractStateStore
      store.set({
        ...current,
        target: eventPayload ?? current.target,
        selected: true,
        inspectedAt: Date.now(),
      });
    }
  }

  private async handleDrilldown(
    action: ViewSpecAction,
    runtime: RuntimeView,
    _eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "drilldown" || !action.targetWidgetId) return;
    const store = runtime.contractStores.get(action.targetWidgetId);
    if (store) {
      const current = (store.get() || {}) as Record<string, unknown>;
      const currentDepth = typeof current.depth === "number" ? current.depth : 0;
      store.set({
        ...current,
        depth: currentDepth + 1,
      });
    }
  }

  private async handleFilter(
    action: ViewSpecAction,
    runtime: RuntimeView,
    eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "filter" || !action.targetWidgetId) return;
    const store = runtime.contractStores.get(action.targetWidgetId);
    if (store) {
      const current = (store.get() || {}) as Record<string, unknown>;
      const currentFilter = (current.filter as Record<string, unknown>) || {};
      const filterPatch = (eventPayload as Record<string, unknown>) || {};
      store.set({
        ...current,
        filter: {
          ...currentFilter,
          ...filterPatch,
        },
      });
    }
  }

  private async handleSort(
    action: ViewSpecAction,
    runtime: RuntimeView,
    eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "sort" || !action.targetWidgetId) return;
    const store = runtime.contractStores.get(action.targetWidgetId);
    if (store) {
      const current = (store.get() || {}) as Record<string, unknown>;
      store.set({
        ...current,
        sort: eventPayload,
      });
    }
  }

  private async handleAcknowledge(
    action: ViewSpecAction,
    runtime: RuntimeView,
    _eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "acknowledge" || !action.targetWidgetId) return;
    const store = runtime.contractStores.get(action.targetWidgetId);
    if (store) {
      const current = (store.get() || {}) as Record<string, unknown>;
      store.set({
        ...current,
        acknowledged: true,
        acknowledgedAt: Date.now(),
      });
    }
  }

  private async handleDismiss(
    action: ViewSpecAction,
    runtime: RuntimeView,
    _eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "dismiss" || !action.targetWidgetId) return;
    const store = runtime.contractStores.get(action.targetWidgetId);
    if (store) {
      const current = (store.get() || {}) as Record<string, unknown>;
      store.set({
        ...current,
        dismissed: true,
      });
    }
  }

  private async handleCompare(
    action: ViewSpecAction,
    runtime: RuntimeView,
    eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "compare" || !action.targetWidgetId) return;
    const store = runtime.contractStores.get(action.targetWidgetId);
    if (store) {
      const current = (store.get() || {}) as Record<string, unknown>;
      store.set({
        ...current,
        compareWith: eventPayload,
      });
    }
  }

  private async handleSelect(
    action: ViewSpecAction,
    runtime: RuntimeView,
    eventPayload?: unknown,
  ): Promise<void> {
    if (action.type !== "select" || !action.targetWidgetId) return;

    if (eventPayload && typeof eventPayload === "object") {
      const payloadObj = eventPayload as Record<string, unknown>;
      const entityId = String(payloadObj.id || payloadObj.entityId || "selected");
      const rowIndex = typeof payloadObj.index === "number" ? payloadObj.index : undefined;
      runtime.interactionContext?.onRowSelect(action.targetWidgetId, entityId, rowIndex);
    }

    const store = runtime.contractStores.get(action.targetWidgetId);
    if (store) {
      const current = (store.get() || {}) as Record<string, unknown>;
      store.set({
        ...current,
        selectedItem: eventPayload,
      });
    }
  }
}
