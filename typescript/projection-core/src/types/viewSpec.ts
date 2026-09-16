import { CapabilityId, PriorityLevel, HierarchySetting, DensitySetting } from "./designIR";
import { CapabilityContract, SurfaceContextContract } from "./capabilities";

export interface ArtifactIdentity {
  artifactId: string;
  artifactVersion: number;
  artifactDigest: `sha256:${string}`;
}

export interface Widget {
  id: string;
  type: string;
  props?: Record<string, any>;
  contract: CapabilityId;
}

export interface WidgetInstance {
  id: string;
  contract: CapabilityContract;
  widget: Widget;
  role?: string;
}

export interface LayoutSpec {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  flex?: number;
  order?: number;
}

export interface LayoutNode {
  id: string;
  widgetId: string;
  region: "main" | "sidebar" | "footer" | "header" | "overlay";
  layout: LayoutSpec;
  priority?: PriorityLevel;
}

export interface LayoutGraph {
  nodes: LayoutNode[];
  hierarchy?: HierarchySetting;
  density?: DensitySetting;
}

export type PayloadSourceType = "rest" | "sse" | "mock" | "file" | "agent" | "server";

export interface PayloadSource {
  type: PayloadSourceType;
  url?: string;
  mock?: any;
  agentId?: string;
  manifest?: import("../adapter/governed").ProjectionManifest;
}

export interface AdapterBinding {
  widgetId: string;
  adapterId: string;
  source: PayloadSource;
  outputContract: CapabilityId;
}

export type ViewSpecAction =
  | { type: "navigate"; target: string }
  | { type: "inspect"; widgetId: string }
  | { type: "drilldown"; widgetId: string }
  | { type: "filter"; widgetId: string; filter: Record<string, any> }
  | { type: "sort"; widgetId: string; sort: { key: string; direction: "asc" | "desc" } }
  | { type: "acknowledge"; widgetId: string }
  | { type: "dismiss"; widgetId: string }
  | { type: "compare"; widgetId: string; targetId: string };

export interface EventRoute {
  fromWidget: string;
  event: string;
  action: ViewSpecAction;
}

export interface EventRoutingMatrix {
  routes: EventRoute[];
  defaultAction?: ViewSpecAction;
}

export interface FixtureOverrides {
  [widgetId: string]: {
    data: any;
    scenario: "nominal" | "empty" | "overflow" | "fuzz";
  };
}

/** Serializable, versioned artifact. Runtime state must not be persisted here. */
export interface ViewSpec extends Partial<ArtifactIdentity> {
  schemaVersion?: 1;
  id: string;
  layout: LayoutGraph;
  widgets: WidgetInstance[];
  adapters: AdapterBinding[];
  events: EventRoute[];
  fixtures?: FixtureOverrides;
  context?: SurfaceContextContract;
  name?: string;
}

export interface ViewRuntimeState {
  mounted: boolean;
  selectedWidgetIds: string[];
  adapterStatuses: Record<string, "idle" | "loading" | "success" | "error">;
}

export function validateViewSpec(spec: unknown): spec is ViewSpec {
  if (!spec || typeof spec !== "object") return false;
  const value = spec as Partial<ViewSpec>;
  return (value.schemaVersion === undefined || value.schemaVersion === 1)
    && typeof value.id === "string"
    && (value.artifactId === undefined || typeof value.artifactId === "string")
    && (value.artifactVersion === undefined || Number.isInteger(value.artifactVersion))
    && (value.artifactDigest === undefined || (typeof value.artifactDigest === "string" && /^sha256:[0-9a-f]{64}$/.test(value.artifactDigest)))
    && Array.isArray(value.layout?.nodes)
    && Array.isArray(value.widgets)
    && Array.isArray(value.adapters)
    && Array.isArray(value.events);
}
