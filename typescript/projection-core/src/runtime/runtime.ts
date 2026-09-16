import { ViewSpec, ViewSpecAction, validateViewSpec } from "../types/viewSpec";
import { runtimeModeState } from "./modes";
import { AdapterRuntime } from "../adapter/runtime";
import { WidgetRegistry } from "./widgetRegistry";
import { SimpleEventBus } from "./eventBus";
import {
  RuntimeView,
  RuntimeWidget,
  RuntimeAdapter,
  RuntimeLayoutNode,
  RuntimeLayoutGraph,
  RuntimeEvent,
  RuntimeOptions,
} from "./types";



export class ViewSpecRuntime {
  private widgetRegistry: WidgetRegistry;
  private adapterRuntime: AdapterRuntime;
  private options: Required<Omit<RuntimeOptions, "actionHandlers" | "navigation">> & Pick<RuntimeOptions, "actionHandlers" | "navigation">;
  private activeViews: Map<string, RuntimeView> = new Map();

  constructor(options: RuntimeOptions = {}) {
    this.widgetRegistry = new WidgetRegistry();
    this.adapterRuntime = new AdapterRuntime();
    this.options = {
      mode: options.mode ?? (options.useFixtures === false ? "live-read" : "fixture"),
      useFixtures: options.useFixtures ?? true,
      refreshInterval: options.refreshInterval ?? 30000,
      actionHandlers: options.actionHandlers,
      navigation: options.navigation,
    };
    this.widgetRegistry.registerDefaults();
  }

  mount(spec: ViewSpec, container: HTMLElement): RuntimeView {
    if (!validateViewSpec(spec)) {
      throw new Error("Invalid or unversioned ViewSpec artifact");
    }
    const view = this.initializeView(spec, container);
    view.container.dataset.runtimeMode = runtimeModeState(view.mode ?? "fixture").label;
    this.buildLayout(view);
    this.instantiateWidgets(view);
    this.runAdapters(view);
    this.wireEvents(view);
    this.renderLayout(view);

    view.mounted = true;
    this.activeViews.set(view.spec.id, view);
    return view;
  }

  private initializeView(spec: ViewSpec, container: HTMLElement): RuntimeView {
    const eventBus = new SimpleEventBus();

    return {
      spec,
      mode: this.options.mode,
      widgets: new Map(),
      adapters: new Map(),
      layout: {
        nodes: new Map(),
        container,
        regions: this.createRegions(container),
      },
      eventBus,
      container,
      mounted: false,
    };
  }

  private createRegions(container: HTMLElement): RuntimeLayoutGraph["regions"] {
    const regions = {
      main: document.createElement("div"),
      sidebar: document.createElement("div"),
      header: document.createElement("div"),
      footer: document.createElement("div"),
      overlay: document.createElement("div"),
    };

    Object.entries(regions).forEach(([key, element]) => {
      element.className = `runtime-region runtime-region-${key}`;
      element.style.display = key === "overlay" ? "none" : "block";
      container.appendChild(element);
    });

    return regions;
  }

  private buildLayout(view: RuntimeView): void {
    for (const node of view.spec.layout.nodes) {
      const layoutNode: RuntimeLayoutNode = {
        id: node.id,
        widgetId: node.widgetId,
        region: node.region,
        layout: node.layout,
        element: undefined,
      };

      const regionContainer = view.layout.regions[node.region];
      if (regionContainer) {
        const element = document.createElement("div");
        element.className = `runtime-widget-container runtime-widget-${node.widgetId}`;
        element.dataset.widgetId = node.widgetId;
        element.dataset.layoutNode = node.id;

        if (node.layout.flex) {
          element.style.flex = String(node.layout.flex);
        }
        if (node.layout.order !== undefined) {
          element.style.order = String(node.layout.order);
        }

        regionContainer.appendChild(element);
        layoutNode.element = element;
      }

      view.layout.nodes.set(node.id, layoutNode);
    }
  }

  private instantiateWidgets(view: RuntimeView): void {
    for (const widgetInstance of view.spec.widgets) {
      const impl = this.widgetRegistry.get(widgetInstance.widget.type);

      if (!impl) {
        console.warn(`Widget implementation not found: ${widgetInstance.widget.type}`);
        continue;
      }

      let props: Record<string, unknown> = {
        ...widgetInstance.widget.props,
        data: this.getWidgetData(view, widgetInstance.id),
      };

      const runtimeWidget: RuntimeWidget = {
        id: widgetInstance.id,
        contract: widgetInstance.contract,
        impl,
        props,
        state: {},
        mounted: false,
      };

      view.widgets.set(widgetInstance.id, runtimeWidget);
    }
  }

  private getWidgetData(view: RuntimeView, widgetId: string): any {
    if (this.options.useFixtures && view.spec.fixtures) {
      const fixture = view.spec.fixtures[widgetId];
      if (fixture) {
        return fixture.data;
      }
    }

    const adapterBinding = view.spec.adapters.find((a) => a.widgetId === widgetId);
    if (adapterBinding) {
      const runtimeAdapter = view.adapters.get(adapterBinding.adapterId);
      if (runtimeAdapter?.lastOutput) {
        return runtimeAdapter.lastOutput;
      }
    }

    return {};
  }

  private async runAdapters(view: RuntimeView): Promise<void> {
    const adapterPromises: Promise<void>[] = [];

    for (const binding of view.spec.adapters) {
      const adapter = this.adapterRuntime.getAdapter(binding.adapterId);

      if (!adapter) {
        // For mock bindings, create a synthetic adapter
        this.adapterRuntime.register({
          id: binding.adapterId,
          source: binding.source as any,
          steps: [],
          outputContract: binding.outputContract as any,
        });
      }

      const runtimeAdapter: RuntimeAdapter = {
        id: binding.adapterId,
        adapter: this.adapterRuntime.getAdapter(binding.adapterId)!,
        binding,
        status: "idle",
        source: null,
      };
      view.adapters.set(binding.adapterId, runtimeAdapter);

      const promise = this.executeAdapter(view, runtimeAdapter);
      adapterPromises.push(promise);
    }

    await Promise.allSettled(adapterPromises);
  }

  private async executeAdapter(view: RuntimeView, runtimeAdapter: RuntimeAdapter): Promise<void> {
    try {
      runtimeAdapter.status = "loading";

      const output = await this.adapterRuntime.execute(
        runtimeAdapter.id,
        runtimeAdapter.binding.source.mock,
      );

      runtimeAdapter.lastOutput = output;
      runtimeAdapter.status = "success";

      const widget = view.widgets.get(runtimeAdapter.binding.widgetId);
      if (widget) {
        widget.props.data = output;
        this.updateWidget(widget, view);
      }
    } catch (error) {
      runtimeAdapter.status = "error";
      runtimeAdapter.lastError = error as Error;
      console.error(`Adapter ${runtimeAdapter.id} failed:`, error);
    }
  }

  private renderLayout(view: RuntimeView): void {
    for (const [, element] of Object.entries(view.layout.regions)) {
      const hasChildren = element.children.length > 0;
      element.style.display = hasChildren ? "block" : "none";
    }

    for (const node of view.layout.nodes.values()) {
      const widget = view.widgets.get(node.widgetId);
      if (widget && node.element) {
        this.renderWidget(widget, node.element);
      }
    }
  }

  private renderWidget(widget: RuntimeWidget, container: HTMLElement): void {
    try {
      container.innerHTML = "";
      widget.impl.render(widget.props, container);
      widget.mounted = true;
      this.attachWidgetEvents(widget, container);
    } catch (error) {
      console.error(`Failed to render widget ${widget.id}:`, error);
      container.innerHTML = `<div class="widget-error">Failed to render widget</div>`;
    }
  }

  private updateWidget(widget: RuntimeWidget, view: RuntimeView): void {
    const node = Array.from(view.layout.nodes.values()).find(
      (n) => n.widgetId === widget.id,
    );

    if (node?.element && widget.impl.update) {
      widget.impl.update(widget.props, node.element);
    } else if (node?.element) {
      this.renderWidget(widget, node.element);
    }
  }

  private attachWidgetEvents(widget: RuntimeWidget, container: HTMLElement): void {
    const handler = (event: Event) => {
      const customEvent = event as CustomEvent;
      if (customEvent.detail?.type) {
        const runtimeEvent: RuntimeEvent = {
          type: customEvent.detail.type,
          source: widget.id,
          payload: customEvent.detail.payload || {},
          timestamp: Date.now(),
        };
        const runtimeView = Array.from(this.activeViews.values()).find((candidate) => candidate.widgets.get(widget.id) === widget);
        runtimeView?.eventBus.emit(runtimeEvent);
      }
    };

    container.addEventListener("widget-event", handler);
  }

  private wireEvents(view: RuntimeView): void {
    for (const route of view.spec.events) {
      view.eventBus.subscribe(route.fromWidget, (event: RuntimeEvent) => {
        if (event.type === route.event) {
          this.executeAction(route.action, view);
        }
      });
    }
  }

  private async executeAction(action: ViewSpecAction, view: RuntimeView): Promise<void> {
    switch (action.type) {
      case "navigate":
        if (!action.target) throw new Error("Navigation target is required");
        console.log(`Navigating to: ${action.target}`);
        view.eventBus.emit({
          type: "navigation",
          source: "runtime",
          payload: { target: action.target },
          timestamp: Date.now(),
        });
        break;
      case "inspect": {
        const widget = Array.from(view.widgets.values()).find(
          (w) => w.id === action.widgetId,
        );
        if (!widget) throw new Error(`Widget not found: ${action.widgetId}`);
        if (widget) {
          widget.props.selected = true;
          this.updateWidget(widget, view);
        }
        break;
      }
      case "drilldown": {
        const w = view.widgets.get(action.widgetId);
        if (w) {
          w.props.depth = ((w.props.depth as number) || 0) + 1;
          this.updateWidget(w, view);
        }
        break;
      }
      case "filter": {
        const fw = view.widgets.get(action.widgetId);
        if (fw) {
          fw.props.filter = action.filter;
          this.refreshWidgetData(action.widgetId, view);
        }
        break;
      }
      case "sort": {
        const sw = view.widgets.get(action.widgetId);
        if (sw) {
          sw.props.sort = action.sort;
          this.refreshWidgetData(action.widgetId, view);
        }
        break;
      }
      case "acknowledge": {
        const aw = view.widgets.get(action.widgetId);
        if (aw) {
          aw.props.acknowledged = true;
          this.updateWidget(aw, view);
        }
        break;
      }
      case "dismiss": {
        const dn = Array.from(view.layout.nodes.values()).find(
          (n) => n.widgetId === action.widgetId,
        );
        if (dn?.element) {
          dn.element.style.display = "none";
        }
        break;
      }
      case "compare": {
        const cw = view.widgets.get(action.widgetId);
        if (cw) {
          cw.props.compareWith = action.targetId;
          this.updateWidget(cw, view);
        }
        break;
      }
    }
  }

  private async refreshWidgetData(widgetId: string, view: RuntimeView): Promise<void> {
    const binding = view.spec.adapters.find((a) => a.widgetId === widgetId);
    if (binding) {
      const runtimeAdapter = view.adapters.get(binding.adapterId);
      if (runtimeAdapter) {
        await this.executeAdapter(view, runtimeAdapter);
      }
    }
  }

  unmount(view: RuntimeView): void {
    for (const [, adapter] of view.adapters) {
      if (adapter.interval) {
        clearInterval(adapter.interval);
      }
      if (adapter.source) {
        adapter.source.close();
      }
    }

    for (const [id, widget] of view.widgets) {
      if (widget.impl.destroy) {
        const node = Array.from(view.layout.nodes.values()).find(
          (n) => n.widgetId === id,
        );
        if (node?.element) {
          widget.impl.destroy(node.element);
        }
      }
    }

    view.container.innerHTML = "";
    this.activeViews.delete(view.spec.id);
    view.mounted = false;
  }

  dispatchAction(view: RuntimeView, action: ViewSpecAction): void {
    const event: RuntimeEvent = {
      type: "action",
      source: "runtime",
      payload: { action },
      timestamp: Date.now(),
    };

    for (const [id, widget] of view.widgets) {
      if (widget.impl.events?.includes("action")) {
        const widgetEvent: RuntimeEvent = {
          ...event,
          source: id,
        };
        view.eventBus.emit(widgetEvent);
      }
    }
  }

  getView(id: string): RuntimeView | undefined {
    return this.activeViews.get(id);
  }

  listViews(): string[] {
    return Array.from(this.activeViews.keys());
  }
}
