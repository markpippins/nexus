import { RuntimeView } from "./types";
import { ViewSpecAction } from "../types/viewSpec";

export interface ActionInterpreter {
  execute(action: ViewSpecAction, runtime: RuntimeView): Promise<void>;
  registerHandler(type: string, handler: ActionHandler): void;
}

export type ActionHandler = (action: ViewSpecAction, runtime: RuntimeView) => Promise<void>;

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
  }

  registerHandler(type: string, handler: ActionHandler): void {
    this.customHandlers.set(type, handler);
  }

  async execute(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    const handler = this.customHandlers.get(action.type);
    if (!handler) {
      throw new Error(`Unsupported action type: ${action.type}`);
    }

    try {
      await handler(action, runtime);
    } catch (error) {
      console.error(`Action execution failed (${action.type}):`, error);
      throw error;
    }
  }

  private async handleNavigate(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    if (action.type !== "navigate") return;
    console.log(`Navigating to: ${action.target}`);

    runtime.eventBus.emit({
      type: "navigation",
      source: "actionInterpreter",
      payload: { target: action.target },
      timestamp: Date.now(),
    });
  }

  private async handleInspect(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    if (action.type !== "inspect") return;
    const targetWidgetId = action.widgetId;
    if (!targetWidgetId) return;

    const widget = Array.from(runtime.widgets.values()).find((w) => w.id === targetWidgetId);
    if (!widget) throw new Error(`Widget not found: ${targetWidgetId}`);
    widget.props.selected = true;
  }

  private async handleDrilldown(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    if (action.type !== "drilldown") return;
    console.log("Drilldown:", action.widgetId);

    const widget = runtime.widgets.get(action.widgetId);
    if (widget) {
      widget.props.depth = ((widget.props.depth as number) || 0) + 1;
    }
  }

  private async handleFilter(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    if (action.type !== "filter") return;
    const widget = runtime.widgets.get(action.widgetId);
    if (widget) {
      widget.props.filter = {
        ...(widget.props.filter as Record<string, any>),
        ...action.filter,
      };
    }
  }

  private async handleSort(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    if (action.type !== "sort") return;
    const widget = runtime.widgets.get(action.widgetId);
    if (widget) {
      widget.props.sort = action.sort;
    }
  }

  private async handleAcknowledge(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    if (action.type !== "acknowledge") return;
    console.log("Acknowledge:", action.widgetId);
    const widget = runtime.widgets.get(action.widgetId);
    if (widget) {
      widget.props.acknowledged = true;
    }
  }

  private async handleDismiss(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    if (action.type !== "dismiss") return;
    console.log("Dismiss:", action.widgetId);
    const node = Array.from(runtime.layout.nodes.values()).find(
      (n) => n.widgetId === action.widgetId,
    );
    if (node?.element) {
      node.element.style.display = "none";
    }
  }

  private async handleCompare(action: ViewSpecAction, runtime: RuntimeView): Promise<void> {
    if (action.type !== "compare") return;
    console.log("Compare:", action.widgetId);
    const widget = runtime.widgets.get(action.widgetId);
    if (widget) {
      widget.props.compareWith = action.targetId || null;
    }
  }
}
