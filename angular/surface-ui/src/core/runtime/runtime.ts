import { ViewSpec, ViewSpecAction } from "../types/viewSpec";
import { AdapterRuntime } from "../adapter/runtime";
import { WidgetRegistry } from "./widgetRegistry";
import { SimpleEventBus } from "./eventBus";
import { InMemoryContractStateStore, ContractStateStore } from "./contractState";
import { DefaultInteractionContextStore } from "./interactionContext";
import { DefaultActionInterpreter, ActionInterpreter } from "./actionInterpreter";
import { generateRuntimeMockData } from "./mockData";
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
  private actionInterpreter: ActionInterpreter;
  private options: Required<Omit<RuntimeOptions, "actionHandlers" | "navigation">> &
    Pick<RuntimeOptions, "actionHandlers" | "navigation">;
  private activeViews: Map<string, RuntimeView> = new Map();

  constructor(options: RuntimeOptions = {}) {
    this.widgetRegistry = new WidgetRegistry();
    this.adapterRuntime = new AdapterRuntime();
    this.actionInterpreter = new DefaultActionInterpreter();
    this.options = {
      useFixtures: true,
      refreshInterval: 30000,
      actionHandlers: options.actionHandlers,
      navigation: options.navigation,
    };
    this.widgetRegistry.registerDefaults();
    if (options.actionHandlers) {
      for (const [type, handler] of Object.entries(options.actionHandlers)) {
        this.actionInterpreter.registerHandler(type, handler);
      }
    }
  }

  mount(spec: ViewSpec, container: HTMLElement): RuntimeView {
    const view = this.initializeView(spec, container);
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
    const contractStores = new Map<string, ContractStateStore>();
    const interactionContext = new DefaultInteractionContextStore(spec.surfaceId || "main");

    for (const widgetInstance of spec.widgets) {
      const initialData = this.getWidgetData(spec, widgetInstance.id);
      contractStores.set(widgetInstance.id, new InMemoryContractStateStore(initialData));
    }

    return {
      spec,
      widgets: new Map(),
      adapters: new Map(),
      contractStores,
      interactionContext,
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

  /**
   * Runtime derives flex, order, and geometry based on region and priority!
   * Compiler does not emit flex or order.
   */
  private buildLayout(view: RuntimeView): void {
    for (const node of view.spec.layout.nodes) {
      const layoutNode: RuntimeLayoutNode = {
        id: node.id,
        widgetId: node.widgetId,
        region: node.region,
        priority: node.priority,
        density: node.density,
        element: undefined,
      };

      const regionContainer = view.layout.regions[node.region];
      if (regionContainer) {
        const element = document.createElement("div");
        element.className = `runtime-widget-container runtime-widget-${node.widgetId}`;
        element.dataset.widgetId = node.widgetId;
        element.dataset.layoutNode = node.id;

        // Runtime calculates flex based on priority
        const flexWeight = node.priority === "primary" ? 2 : node.priority === "ambient" ? 0.5 : 1;
        element.style.flex = String(flexWeight);

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

      const store = view.contractStores.get(widgetInstance.id);
      const props: Record<string, unknown> = {
        ...widgetInstance.widget.props,
        data: store ? store.get() : this.getWidgetData(view.spec, widgetInstance.id),
      };

      const runtimeWidget: RuntimeWidget = {
        id: widgetInstance.id,
        contract: widgetInstance.contract,
        impl,
        props,
        state: {},
        mounted: false,
      };

      // Subscribe widget presentation strictly to its ContractStateStore
      if (store) {
        store.subscribe((nextContractState) => {
          runtimeWidget.props.data = nextContractState;
          if (runtimeWidget.mounted) {
            this.updateWidget(runtimeWidget, view);
          }
        });
      }

      view.widgets.set(widgetInstance.id, runtimeWidget);
    }
  }

  /**
   * Runtime synthesizes sample/fixture data using isolated runtime provider
   */
  private getWidgetData(spec: ViewSpec, widgetId: string): unknown {
    const widget = spec.widgets.find((w) => w.id === widgetId);
    if (!widget) return {};

    const capabilityId = widget.widget.contract;
    return generateRuntimeMockData(capabilityId, widget.role || widgetId);
  }

  private async runAdapters(view: RuntimeView): Promise<void> {
    const adapterPromises: Promise<void>[] = [];

    for (const binding of view.spec.adapters) {
      if (!binding.adapterId) continue;

      let adapter = this.adapterRuntime.getAdapter(binding.adapterId);

      if (!adapter) {
        this.adapterRuntime.register({
          id: binding.adapterId,
          steps: [],
          outputContract: binding.outputContract,
        });
        adapter = this.adapterRuntime.getAdapter(binding.adapterId);
      }

      if (!adapter) continue;

      const runtimeAdapter: RuntimeAdapter = {
        id: binding.adapterId,
        adapter,
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

      // Execute adapter against runtime generated mock / test feed
      const widget = view.spec.widgets.find((w) => w.id === runtimeAdapter.binding.widgetId);
      const rawPayload = widget
        ? generateRuntimeMockData(widget.widget.contract, widget.role || widget.id)
        : {};

      const output = await this.adapterRuntime.execute(runtimeAdapter.id, rawPayload);

      runtimeAdapter.lastOutput = output;
      runtimeAdapter.status = "success";

      // Project adapter output strictly into ContractStateStore
      const store = view.contractStores.get(runtimeAdapter.binding.widgetId);
      if (store) {
        store.replace(output);
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
    const node = Array.from(view.layout.nodes.values()).find((n) => n.widgetId === widget.id);

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
        void runtimeEvent;
      }
    };

    container.addEventListener("widget-event", handler);
  }

  private wireEvents(view: RuntimeView): void {
    for (const route of view.spec.events) {
      view.eventBus.subscribe(route.fromWidget, (event: RuntimeEvent) => {
        if (event.type === route.event) {
          this.executeAction(route.action, view, event.payload);
        }
      });
    }
  }

  private async executeAction(
    action: ViewSpecAction,
    view: RuntimeView,
    eventPayload?: unknown,
  ): Promise<void> {
    await this.actionInterpreter.execute(action, view, eventPayload);
  }

  destroy(viewId: string): void {
    const view = this.activeViews.get(viewId);
    if (!view) return;

    for (const widget of view.widgets.values()) {
      if (widget.impl.destroy && widget.element) {
        widget.impl.destroy(widget.element);
      }
    }

    for (const adapter of view.adapters.values()) {
      if (adapter.interval) {
        clearInterval(adapter.interval);
      }
      if (adapter.source) {
        adapter.source.close();
      }
    }

    view.container.innerHTML = "";
    this.activeViews.delete(viewId);
  }
}
