export type CapabilityId =
  | "MetricSeries"
  | "EntityCollection"
  | "StatusBoard"
  | "Timeline"
  | "KeyMetricMatrix"
  | "ConsensusMatrix"
  | "InspectorPanel"
  | "AuditStream"
  | "WorkQueue"
  | "SurfaceContext";

export type InteractionVerb =
  | "inspect"
  | "drilldown"
  | "compare"
  | "acknowledge"
  | "dismiss"
  | "navigate"
  | "filter"
  | "sort"
  | "select"
  | "multiSelect"
  | "reorder";

export type DensitySetting = "compact" | "normal" | "spacious" | "highSalience";
export type PriorityLevel = "primary" | "secondary" | "ambient";
export type InteractionScope = "local" | "global" | "crossRole" | "crossSurface";
export type VisibilityConstraint = "always" | "whenData" | "whenSelected";
export type LayoutBias = "main" | "sidebar" | "footer" | "header" | "overlay";
export type SurfaceType =
  "dashboard" | "workbench" | "inspector" | "timelineView" | "switchboard" | "matrix";
export type TimeSensitivity = "realTime" | "nearRealTime" | "batch" | "historical";
export type ReliabilityBias = "eventual" | "strong" | "strict";

export interface SchemaHint {
  fields?: string[];
  keys?: string[];
}

export interface CapabilityRef {
  id: CapabilityId;
  variant?: string;
  schemaHint?: SchemaHint;
}

export interface ConstraintSet {
  maxItems?: number;
  minItems?: number;
  requireSelection?: boolean;
  allowMultiSelect?: boolean;
  allowReorder?: boolean;
  allowInlineEdit?: boolean;
  readOnly?: boolean;
  visibility?: VisibilityConstraint;
  layoutBias?: LayoutBias;
  widgetVariant?: string;
  density?: DensitySetting;
}

export interface RoleSpec {
  label?: string;
  capability: CapabilityRef;
  priority?: PriorityLevel;
  density?: DensitySetting;
  interactions?: InteractionVerb[];
  constraints?: ConstraintSet;
}

export interface InteractionSpec {
  verb: InteractionVerb;
  sourceSurface?: string;
  sourceRole?: string;
  targetSurface?: string;
  targetRole?: string;
  scope?: InteractionScope;
  constraints?: ConstraintSet;
}

export interface HierarchySetting {
  primaryRoles?: string[];
  secondaryRoles?: string[];
  ambientRoles?: string[];
}

export interface GlobalContextSpec {
  timeRange?: string;
  environment?: string;
  tenantId?: string;
  timeSensitivity?: TimeSensitivity;
  reliabilityBias?: ReliabilityBias;
  filters?: Record<string, unknown>;
  focusId?: string;
}

export interface SurfaceContextSpec {
  surfaceType?: SurfaceType;
  timeSensitivity?: TimeSensitivity;
  reliabilityBias?: ReliabilityBias;
  filters?: Record<string, unknown>;
  focusRole?: string;
}

export type ContextSpec = SurfaceContextSpec;

export interface SurfaceSpec {
  id: string;
  name?: string;
  description?: string;
  kind?: "dashboard" | "workbench" | "timeline" | "matrix" | "switchboard" | "inspector";
  roles: Record<string, RoleSpec>;
  hierarchy: HierarchySetting;
  interactions: InteractionSpec[];
  density?: DensitySetting;
  constraints?: ConstraintSet;
  context?: SurfaceContextSpec;
}

export type WorkflowEntryAction =
  | { type: "navigateSurface"; toSurfaceId: string }
  | { type: "focusRole"; roleId: string }
  | { type: "setGlobalFilter"; filter: Record<string, unknown> }
  | { type: "setLocalFilter"; roleId: string; filter: Record<string, unknown> }
  | { type: "openInspector"; roleId: string }
  | { type: "selectEntity"; roleId: string; selectionKey: string };

export interface WorkflowStep {
  id: string;
  name?: string;
  surfaceId: string;
  focusRoleId?: string;
  applyContext?: Partial<GlobalContextSpec>;
  entryAction?: WorkflowEntryAction;
  entryActions?: WorkflowEntryAction[];
}

export interface WorkflowSpec {
  id: string;
  name?: string;
  description?: string;
  steps: WorkflowStep[];
}

/**
 * Top-level DesignIR Document
 * Supports both single-surface backward compatibility (roles + hierarchy + interactions)
 * and rich multi-surface orchestration with global context and declarative workflows.
 */
export interface DesignIR {
  id?: string;
  name?: string;
  description?: string;

  // Single-surface direct declaration (backward compatible)
  roles?: Record<string, RoleSpec>;
  interactions?: InteractionSpec[];
  density?: DensitySetting;
  hierarchy?: HierarchySetting;
  constraints?: ConstraintSet;
  context?: ContextSpec;

  // Multi-surface declarations
  surfaces?: Record<string, SurfaceSpec>;
  globalContext?: GlobalContextSpec;
  workflows?: WorkflowSpec[];
}
