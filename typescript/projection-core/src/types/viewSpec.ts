import { CapabilityId, PriorityLevel, HierarchySetting, DensitySetting } from "./designIR";
import { CapabilityContract, SurfaceContextContract } from "./capabilities";

/**
 * ArtifactIdentity — governance provenance applied to compiled artifacts.
 * Serializable, versioned, content-addressed (sha256). Emitted by the
 * governed projection path; validated by validateViewSpec / validateProjectionManifest.
 */
export interface ArtifactIdentity {
  artifactId: string;
  artifactVersion: number;
  artifactDigest: `sha256:${string}`;
}

export interface Widget {
  id: string;
  type: string;
  props?: Record<string, unknown>;
  contract: CapabilityId;
}

export interface WidgetInstance {
  id: string;
  contract: CapabilityContract;
  widget: Widget;
  role?: string;
}

export interface LayoutNode {
  id: string;
  widgetId: string;
  region: "main" | "sidebar" | "footer" | "header" | "overlay";
  priority?: PriorityLevel;
  density?: DensitySetting;
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
  mock?: unknown;
  agentId?: string;
  manifest?: import("../adapter/governed").ProjectionManifest;
}

export interface AdapterStub {
  steps: Array<{
    op: string;
    args?: Record<string, unknown>;
  }>;
}

/**
 * AdapterBinding — a widget's projection from a payload source into a
 * capability contract. `source` is present on governed/server bindings
 * (see adapter/governed); stub-based bindings (studio authoring) may omit it.
 */
export interface AdapterBinding {
  widgetId: string;
  adapterId?: string;
  outputContract: CapabilityId;
  stub?: AdapterStub;
  source?: PayloadSource;
}

/**
 * Abstract ViewSpec action identifiers emitted by compiler
 */
export type ViewSpecAction =
  | { type: "navigate"; target?: string }
  | { type: "inspect"; targetWidgetId?: string }
  | { type: "drilldown"; targetWidgetId?: string }
  | { type: "filter"; targetWidgetId?: string }
  | { type: "sort"; targetWidgetId?: string }
  | { type: "acknowledge"; targetWidgetId?: string }
  | { type: "dismiss"; targetWidgetId?: string }
  | { type: "compare"; targetWidgetId?: string }
  | { type: "select"; targetWidgetId?: string }
  | { type: "custom"; actionId: string };

export interface EventRoute {
  fromWidget: string;
  event: string;
  action: ViewSpecAction;
}

export interface EventRoutingMatrix {
  routes: EventRoute[];
  defaultAction?: ViewSpecAction;
}

/**
 * Structural Fixture declaration emitted by compiler (no mock payload data)
 */
export interface StructuralFixtureSpec {
  scenario: "nominal" | "empty" | "overflow" | "fuzz";
  contract: CapabilityId;
}

export interface FixtureOverrides {
  [widgetId: string]: StructuralFixtureSpec;
}

export interface AbstractWorkflowStep {
  id: string;
  name?: string;
  surfaceId: string;
  focusRoleId?: string;
  focusWidgetId?: string;
  contextScope?: string;
}

export interface WorkflowRoutingTable {
  [workflowId: string]: {
    name?: string;
    description?: string;
    steps: AbstractWorkflowStep[];
  };
}

/**
 * ViewSpec: Compiled Executable Program AST
 * Canonical Invariant: ViewSpec is always treated as a compiled program AST,
 * not just a static node configuration. It specifies spatial layout execution,
 * contract bindings, adapter projections, and event routing state graphs.
 * Governed projections extend it with ArtifactIdentity provenance.
 */
export interface ViewSpec extends Partial<ArtifactIdentity> {
  id: string;
  name?: string;
  description?: string;
  surfaceId?: string;
  schemaVersion?: 1;
  layout: LayoutGraph;
  widgets: WidgetInstance[];
  adapters: AdapterBinding[];
  events: EventRoute[];
  fixtures?: FixtureOverrides;
  context?: SurfaceContextContract;
}

/**
 * MultiSurfaceViewSpec: Orchestrates multiple surfaces, shared context, and workflows
 */
export interface MultiSurfaceViewSpec {
  id: string;
  name?: string;
  description?: string;
  surfaces: Record<string, ViewSpec>;
  globalContext?: Record<string, unknown>;
  workflows?: WorkflowRoutingTable;
  activeSurfaceId?: string;
}

/**
 * Structural + provenance validation for a compiled ViewSpec.
 * Guards the canonical Invariant that a ViewSpec is a well-formed program AST
 * with optional ArtifactIdentity provenance attached.
 */
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