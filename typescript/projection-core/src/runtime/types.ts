import { ViewSpec, LayoutSpec, AdapterBinding } from "../types/viewSpec";
import { CapabilityContract } from "../types/capabilities";
import { Adapter } from "../adapter/types";
import type { ActionHandler } from "./actionInterpreter";
import type { ViewRuntimeMode } from "./modes";

export interface RuntimeWidget {
  id: string;
  contract: CapabilityContract;
  impl: WidgetImplementation;
  props: Record<string, unknown>;
  state: Record<string, unknown>;
  element?: HTMLElement;
  mounted: boolean;
}

export interface RuntimeAdapter {
  id: string;
  adapter: Adapter;
  binding: AdapterBinding;
  lastOutput?: unknown;
  lastError?: Error;
  status: "idle" | "loading" | "success" | "error";
  source: EventSource | null;
  interval?: ReturnType<typeof setInterval>;
}

export interface RuntimeLayoutNode {
  id: string;
  widgetId: string;
  region: "main" | "sidebar" | "header" | "footer" | "overlay";
  layout: LayoutSpec;
  element?: HTMLElement;
}

export interface RuntimeLayoutGraph {
  nodes: Map<string, RuntimeLayoutNode>;
  container?: HTMLElement;
  regions: {
    main: HTMLElement;
    sidebar: HTMLElement;
    header: HTMLElement;
    footer: HTMLElement;
    overlay: HTMLElement;
  };
}

export interface RuntimeView {
  spec: ViewSpec;
  mode?: ViewRuntimeMode;
  widgets: Map<string, RuntimeWidget>;
  adapters: Map<string, RuntimeAdapter>;
  layout: RuntimeLayoutGraph;
  eventBus: EventBus;
  container: HTMLElement;
  mounted: boolean;
}

export interface EventBus {
  subscribe(widgetId: string, handler: EventHandler): void;
  unsubscribe(widgetId: string, handler: EventHandler): void;
  emit(event: RuntimeEvent): void;
  on(event: string, handler: EventHandler): void;
  off(event: string, handler: EventHandler): void;
}

export interface RuntimeEvent {
  type: string;
  source: string;
  payload?: Record<string, unknown>;
  timestamp: number;
}

export type EventHandler = (event: RuntimeEvent) => void;

export interface WidgetImplementation {
  render: (props: Record<string, unknown>, container: HTMLElement) => void;
  update?: (props: Record<string, unknown>, container: HTMLElement) => void;
  destroy?: (container: HTMLElement) => void;
  events?: string[];
}


export interface RuntimeOptions {
  mode?: ViewRuntimeMode;
  useFixtures?: boolean;
  refreshInterval?: number;
  actionHandlers?: Record<string, ActionHandler>;
  navigation?: {
    navigate: (to: string, replace?: boolean) => Promise<void>;
  };
}


