import {
  DesignIR,
  SurfaceSpec,
  GlobalContextSpec,
  SurfaceContextSpec,
  WorkflowSpec,
  RoleSpec,
  InteractionSpec,
  DensitySetting,
  PriorityLevel,
  CapabilityId,
  HierarchySetting,
} from "../types/designIR";
import {
  ViewSpec,
  MultiSurfaceViewSpec,
  LayoutNode,
  WidgetInstance,
  AdapterBinding,
  EventRoute,
  FixtureOverrides,
  ViewSpecAction,
  WorkflowRoutingTable,
  AbstractWorkflowStep,
} from "../types/viewSpec";
import { ResolvedCapability, CapabilityContract } from "../types/capabilities";
import { selectWidgetDeterministically, CANONICAL_WIDGET_CATALOG } from "./widgetSelector";
import { suggestAdapterStub } from "./adapterHeuristics";

function sanitizeId(str: string): string {
  return str
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

/**
 * Deterministic hash for AST identity
 */
export function deterministicHash(obj: unknown): string {
  const str = JSON.stringify(obj);
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0;
  }
  return Math.abs(hash).toString(36);
}

export interface ValidationResult {
  ok: boolean;
  errors?: string[];
}

/**
 * Phase 1: Semantic & Structural Validation
 * Validates IR schema, role declarations, capability IDs, and references.
 */
export function validateDesignIR(ir: DesignIR): ValidationResult {
  const errors: string[] = [];

  if (!ir || typeof ir !== "object") {
    return { ok: false, errors: ["DesignIR must be an object"] };
  }

  // Multi-surface document validation
  if (ir.surfaces && Object.keys(ir.surfaces).length > 0) {
    for (const [surfaceId, surface] of Object.entries(ir.surfaces)) {
      if (!surface.roles || Object.keys(surface.roles).length === 0) {
        errors.push(`Surface "${surfaceId}" must declare at least one role`);
      }
      for (const [roleName, role] of Object.entries(surface.roles || {})) {
        if (!role.capability?.id) {
          errors.push(
            `Surface "${surfaceId}" role "${roleName}" has missing or invalid capability id`,
          );
        }
      }
      for (const interaction of surface.interactions || []) {
        if (interaction.sourceRole && !surface.roles[interaction.sourceRole]) {
          errors.push(
            `Surface "${surfaceId}" interaction sourceRole "${interaction.sourceRole}" not found in roles`,
          );
        }
      }
    }

    // Validate workflows
    for (const workflow of ir.workflows || []) {
      for (const step of workflow.steps || []) {
        if (!ir.surfaces[step.surfaceId]) {
          errors.push(
            `Workflow "${workflow.id}" step references non-existent surface "${step.surfaceId}"`,
          );
        }
      }
    }
  } else {
    // Single-surface direct declaration validation
    if (!ir.roles || Object.keys(ir.roles).length === 0) {
      errors.push("DesignIR must declare at least one role in 'roles' or define 'surfaces'");
    }
    for (const [roleName, role] of Object.entries(ir.roles || {})) {
      if (!role.capability?.id) {
        errors.push(`Role "${roleName}" has missing or invalid capability id`);
      }
    }
    for (const interaction of ir.interactions || []) {
      if (interaction.sourceRole && ir.roles && !ir.roles[interaction.sourceRole]) {
        errors.push(`Interaction sourceRole "${interaction.sourceRole}" not found in roles`);
      }
      if (interaction.targetRole && ir.roles && !ir.roles[interaction.targetRole]) {
        errors.push(`Interaction targetRole "${interaction.targetRole}" not found in roles`);
      }
    }
  }

  return {
    ok: errors.length === 0,
    errors: errors.length > 0 ? errors : undefined,
  };
}

/**
 * Phase 2: Capability & Contract Resolution
 * Pure resolution of role capability contracts.
 */
export function resolveCapabilities(roles: Record<string, RoleSpec>): ResolvedCapability[] {
  const resolved: ResolvedCapability[] = [];

  for (const [roleName, roleSpec] of Object.entries(roles)) {
    const contract = createContractStub(roleSpec.capability.id);
    resolved.push({
      role: roleName,
      contract,
      capabilityRef: roleSpec.capability,
      constraints: roleSpec.constraints,
    });
  }

  return resolved;
}

export function createContractStub(capabilityId: CapabilityId): CapabilityContract {
  switch (capabilityId) {
    case "MetricSeries":
      return { points: [], unit: undefined, status: "ok" };
    case "EntityCollection":
      return { items: [], columns: [] };
    case "StatusBoard":
      return { stages: [], items: [] };
    case "Timeline":
      return { events: [] };
    case "KeyMetricMatrix":
      return { metrics: [] };
    case "ConsensusMatrix":
      return { items: [] };
    case "InspectorPanel":
      return { target: undefined, fields: [] };
    case "AuditStream":
      return { entries: [] };
    case "WorkQueue":
      return { items: [] };
    case "SurfaceContext":
      return {
        surfaceType: "dashboard",
        timeSensitivity: "nearRealTime",
        reliabilityBias: "strong",
      };
    default:
      return {
        surfaceType: "dashboard",
        timeSensitivity: "nearRealTime",
        reliabilityBias: "strong",
      };
  }
}

/**
 * Phase 3: Widget Selection
 * Deterministic scoring and catalog binding.
 */
export function selectWidgets(
  caps: ResolvedCapability[],
  roles: Record<string, RoleSpec>,
  defaultDensity: DensitySetting = "normal",
  context?: SurfaceContextSpec,
): WidgetInstance[] {
  const instances: WidgetInstance[] = [];

  for (const cap of caps) {
    const roleSpec = roles[cap.role];
    const effectiveDensity: DensitySetting =
      roleSpec.constraints?.density || roleSpec.density || defaultDensity;

    const { selected } = selectWidgetDeterministically(
      cap.role,
      roleSpec,
      effectiveDensity,
      context,
      CANONICAL_WIDGET_CATALOG,
    );

    const widgetId = `widget-${sanitizeId(cap.role)}`;

    instances.push({
      id: widgetId,
      contract: cap.contract,
      widget: {
        id: selected.id,
        type: selected.id,
        contract: selected.capabilityId,
        props: {
          variant: selected.variant,
          density: effectiveDensity,
          priority: roleSpec.priority || "secondary",
        },
      },
      role: cap.role,
    });
  }

  return instances;
}

/**
 * Phase 4: Spatial Layout Synthesis
 * Emits structural layout nodes only (region, priority, density).
 * Invariant: Never emit CSS, flexbox, pixel geometry, or runtime hints.
 */
export function synthesizeLayout(
  widgets: WidgetInstance[],
  hierarchy: HierarchySetting = {},
  roles: Record<string, RoleSpec> = {},
  density: DensitySetting = "normal",
): { nodes: LayoutNode[]; hierarchy: HierarchySetting; density: DensitySetting } {
  const nodes: LayoutNode[] = [];
  const priorityMap: Record<string, number> = {};

  for (const role of hierarchy.primaryRoles || []) priorityMap[role] = 0;
  for (const role of hierarchy.secondaryRoles || []) priorityMap[role] = 1;
  for (const role of hierarchy.ambientRoles || []) priorityMap[role] = 2;

  for (const widget of widgets) {
    if (!widget.role) continue;
    if (!(widget.role in priorityMap)) {
      const roleSpec = roles[widget.role];
      if (roleSpec?.priority === "primary") priorityMap[widget.role] = 0;
      else if (roleSpec?.priority === "secondary") priorityMap[widget.role] = 1;
      else priorityMap[widget.role] = 2;
    }
  }

  // Deterministic sort: priority ASC, then role ID ASC
  const sortedWidgets = [...widgets].sort((a, b) => {
    const aPriority = a.role ? (priorityMap[a.role] ?? 1) : 1;
    const bPriority = b.role ? (priorityMap[b.role] ?? 1) : 1;
    if (aPriority !== bPriority) return aPriority - bPriority;
    return a.id.localeCompare(b.id);
  });

  const priorityLevels: PriorityLevel[] = ["primary", "secondary", "ambient"];

  for (const widget of sortedWidgets) {
    const roleSpec = widget.role ? roles[widget.role] : undefined;
    const priorityIndex =
      widget.role && priorityMap[widget.role] !== undefined ? priorityMap[widget.role] : 1;
    const priority: PriorityLevel = priorityLevels[priorityIndex] ?? "secondary";

    const bias =
      roleSpec?.constraints?.layoutBias ?? deriveDefaultLayoutBias(widget.widget.contract);

    nodes.push({
      id: `node-${sanitizeId(widget.role || widget.id)}`,
      widgetId: widget.id,
      region: bias,
      priority,
      density: roleSpec?.constraints?.density || roleSpec?.density || density,
    });
  }

  return {
    nodes,
    hierarchy,
    density,
  };
}

function deriveDefaultLayoutBias(capability: CapabilityId): LayoutNode["region"] {
  switch (capability) {
    case "KeyMetricMatrix":
      return "header";
    case "InspectorPanel":
      return "sidebar";
    case "AuditStream":
      return "footer";
    default:
      return "main";
  }
}

/**
 * Phase 5: Adapter Binding & Suggestions
 * Emits adapter stubs and bindings only.
 * Invariant: Never generate mock payload data or bind to live endpoints.
 */
export function bindAdapters(
  widgets: WidgetInstance[],
  roles: Record<string, RoleSpec>,
  interactions: InteractionSpec[] = [],
  effectiveDensity: DensitySetting = "normal",
  context?: SurfaceContextSpec,
): AdapterBinding[] {
  const bindings: AdapterBinding[] = [];

  for (const widget of widgets) {
    const role = widget.role ? roles[widget.role] : undefined;
    const capabilityId = widget.widget.contract;

    const stub = role
      ? suggestAdapterStub(capabilityId, role, interactions, effectiveDensity, context)
      : undefined;

    bindings.push({
      widgetId: widget.id,
      adapterId: `adapter-${sanitizeId(widget.role || "item")}-${sanitizeId(capabilityId)}`,
      outputContract: capabilityId,
      stub,
    });
  }

  return bindings;
}

/**
 * Phase 6: Event Routing Synthesis
 * Emits abstract ViewSpecAction identifiers.
 * Invariant: Never emit runtime action payloads, navigation commands, or state mutations.
 */
export function synthesizeEventRouting(
  interactions: InteractionSpec[] = [],
  widgets: WidgetInstance[],
): EventRoute[] {
  const routes: EventRoute[] = [];

  for (const interaction of interactions) {
    const sourceRole = interaction.sourceRole;
    if (!sourceRole) continue;

    const sourceWidget = widgets.find((w) => w.role === sourceRole);
    if (!sourceWidget) continue;

    const targetWidget = interaction.targetRole
      ? widgets.find((w) => w.role === interaction.targetRole)
      : undefined;

    let action: ViewSpecAction;

    switch (interaction.verb) {
      case "navigate":
        action = {
          type: "navigate",
          target: interaction.targetSurface || interaction.targetRole,
        };
        break;
      case "inspect":
        action = { type: "inspect", targetWidgetId: targetWidget?.id };
        break;
      case "drilldown":
        action = { type: "drilldown", targetWidgetId: targetWidget?.id };
        break;
      case "filter":
        action = { type: "filter", targetWidgetId: targetWidget?.id };
        break;
      case "sort":
        action = { type: "sort", targetWidgetId: targetWidget?.id };
        break;
      case "acknowledge":
        action = { type: "acknowledge", targetWidgetId: targetWidget?.id };
        break;
      case "dismiss":
        action = { type: "dismiss", targetWidgetId: targetWidget?.id };
        break;
      case "compare":
        action = { type: "compare", targetWidgetId: targetWidget?.id };
        break;
      case "select":
        action = { type: "select", targetWidgetId: targetWidget?.id };
        break;
      default:
        action = { type: "inspect", targetWidgetId: targetWidget?.id };
    }

    routes.push({
      fromWidget: sourceWidget.id,
      event: interaction.verb,
      action,
    });
  }

  return routes;
}

/**
 * Phase 7: Fixture Synthesis (Structural only, no mock payloads)
 * Emits structural fixture scenarios and contract targets.
 */
export function synthesizeFixtures(caps: ResolvedCapability[]): FixtureOverrides {
  const fixtures: FixtureOverrides = {};

  for (const cap of caps) {
    const widgetId = `widget-${sanitizeId(cap.role)}`;
    fixtures[widgetId] = {
      scenario: "nominal",
      contract: cap.capabilityRef.id,
    };
  }

  return fixtures;
}

/**
 * Workflow Lowering: WorkflowSpec -> Abstract WorkflowRoutingTable
 * Invariant: Emits abstract workflow steps and references, never runtime state mutations.
 */
export function synthesizeWorkflows(
  workflows: WorkflowSpec[] = [],
  _surfaces: Record<string, SurfaceSpec> = {},
): WorkflowRoutingTable {
  const table: WorkflowRoutingTable = {};

  for (const wf of workflows) {
    const steps: AbstractWorkflowStep[] = [];

    for (const step of wf.steps) {
      steps.push({
        id: step.id,
        name: step.name || step.id,
        surfaceId: step.surfaceId,
        focusRoleId: step.focusRoleId,
        focusWidgetId: step.focusRoleId ? `widget-${sanitizeId(step.focusRoleId)}` : undefined,
        contextScope: step.applyContext ? JSON.stringify(step.applyContext) : undefined,
      });
    }

    table[wf.id] = {
      name: wf.name || wf.id,
      description: wf.description,
      steps,
    };
  }

  return table;
}

/**
 * Pure Deterministic Compiler for Single Surface
 */
export function compileSurfaceSpec(
  surfaceId: string,
  surface: SurfaceSpec,
  globalContext?: GlobalContextSpec,
): ViewSpec {
  const effectiveContext: SurfaceContextSpec = {
    surfaceType: surface.kind || "dashboard",
    timeSensitivity:
      surface.context?.timeSensitivity || globalContext?.timeSensitivity || "nearRealTime",
    reliabilityBias: surface.context?.reliabilityBias || globalContext?.reliabilityBias || "strong",
    filters: { ...(globalContext?.filters || {}), ...(surface.context?.filters || {}) },
  };

  const caps = resolveCapabilities(surface.roles);
  const density = surface.density || "normal";
  const widgets = selectWidgets(caps, surface.roles, density, effectiveContext);
  const layout = synthesizeLayout(widgets, surface.hierarchy, surface.roles, density);
  const adapters = bindAdapters(
    widgets,
    surface.roles,
    surface.interactions,
    density,
    effectiveContext,
  );
  const events = synthesizeEventRouting(surface.interactions, widgets);
  const fixtures = synthesizeFixtures(caps);

  return {
    id: `viewspec-${sanitizeId(surfaceId)}`,
    name: surface.name || surfaceId,
    description: surface.description,
    surfaceId,
    layout,
    widgets,
    adapters,
    events,
    fixtures,
    context: {
      surfaceType: effectiveContext.surfaceType || "dashboard",
      timeSensitivity: effectiveContext.timeSensitivity || "nearRealTime",
      reliabilityBias: effectiveContext.reliabilityBias || "strong",
    },
  };
}

/**
 * Pure Deterministic Compiler: DesignIR -> MultiSurfaceViewSpec
 * Invariant 1: Top-level compilation returns MultiSurfaceViewSpec.
 * Invariant 9: Multi-surface compilation produces all surfaces and workflow routing table.
 */
export function compileDesignIR(ir: DesignIR): MultiSurfaceViewSpec {
  const validation = validateDesignIR(ir);
  if (!validation.ok) {
    throw new Error(`[DesignIR Compiler] ${validation.errors?.join("; ")}`);
  }

  const surfaces: Record<string, ViewSpec> = {};

  if (ir.surfaces && Object.keys(ir.surfaces).length > 0) {
    for (const [id, surface] of Object.entries(ir.surfaces)) {
      surfaces[id] = compileSurfaceSpec(id, surface, ir.globalContext);
    }
  } else {
    // Single-surface document fallback
    const roles = ir.roles || {};
    const hierarchy = ir.hierarchy || {};
    const interactions = ir.interactions || [];
    const density = ir.density || "normal";
    const context = ir.context;

    const caps = resolveCapabilities(roles);
    const widgets = selectWidgets(caps, roles, density, context);
    const layout = synthesizeLayout(widgets, hierarchy, roles, density);
    const adapters = bindAdapters(widgets, roles, interactions, density, context);
    const events = synthesizeEventRouting(interactions, widgets);
    const fixtures = synthesizeFixtures(caps);

    const mainSurfaceSpec: ViewSpec = {
      id: ir.id || `viewspec-${deterministicHash(ir)}`,
      name: ir.name || "Main Surface",
      description: ir.description,
      surfaceId: "main",
      layout,
      widgets,
      adapters,
      events,
      fixtures,
      context: context
        ? {
            surfaceType: context.surfaceType || "dashboard",
            timeSensitivity: context.timeSensitivity || "nearRealTime",
            reliabilityBias: context.reliabilityBias || "strong",
          }
        : undefined,
    };

    surfaces["main"] = mainSurfaceSpec;
  }

  const workflows = synthesizeWorkflows(ir.workflows, ir.surfaces);

  return {
    id: ir.id || `multisurface-${deterministicHash(ir)}`,
    name: ir.name,
    description: ir.description,
    surfaces,
    globalContext: ir.globalContext as Record<string, unknown>,
    workflows,
    activeSurfaceId: Object.keys(surfaces)[0],
  };
}

/**
 * Alias for compileDesignIR
 */
export const compileMultiSurfaceDesignIR = compileDesignIR;
