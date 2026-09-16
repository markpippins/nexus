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
export type InteractionScope = "local" | "global" | "crossRole";
export type VisibilityConstraint = "always" | "whenData" | "whenSelected";
export type LayoutBias = "main" | "sidebar" | "footer" | "header" | "overlay";
export type SurfaceType = "dashboard" | "workbench" | "inspector" | "timelineView";
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
  sourceRole?: string;
  targetRole?: string;
  scope?: InteractionScope;
  constraints?: ConstraintSet;
}

export interface HierarchySetting {
  primaryRoles?: string[];
  secondaryRoles?: string[];
  ambientRoles?: string[];
}

export interface ContextSpec {
  surfaceType?: SurfaceType;
  timeSensitivity?: TimeSensitivity;
  reliabilityBias?: ReliabilityBias;
}

export interface DesignIR {
  id?: string;
  name?: string;
  roles: Record<string, RoleSpec>;
  interactions: InteractionSpec[];
  density?: DensitySetting;
  hierarchy?: HierarchySetting;
  constraints?: ConstraintSet;
  context?: ContextSpec;
}
