import {
  DesignIR,
  SurfaceSpec,
  RoleSpec,
  InteractionSpec,
  HierarchySetting,
  DensitySetting,
  GlobalContextSpec,
} from "../types/designIR";
import {
  ViewSpec,
  MultiSurfaceViewSpec,
  LayoutNode,
  WidgetInstance,
  AdapterBinding,
  EventRoute,
  FixtureOverrides,
  WorkflowRoutingTable,
} from "../types/viewSpec";
import {
  compileSurfaceSpec,
  deterministicHash,
  resolveCapabilities,
  selectWidgets,
  synthesizeLayout,
  bindAdapters,
  synthesizeEventRouting,
  synthesizeFixtures,
  synthesizeWorkflows,
  validateDesignIR,
} from "./compiler";

export type GranularPatchType =
  | "LAYOUT_ONLY"
  | "WIDGETS_AND_ADAPTERS"
  | "EVENTS_ONLY"
  | "WORKFLOWS_ONLY"
  | "SURFACE_FULL"
  | "SURFACE_ADDED"
  | "SURFACE_REMOVED";

export interface GranularSurfacePatch {
  surfaceId: string;
  patchType: GranularPatchType;
  layout?: LayoutNode[];
  widgets?: WidgetInstance[];
  adapters?: AdapterBinding[];
  events?: EventRoute[];
  fixtures?: FixtureOverrides;
}

export interface IncrementalCompilationResult {
  spec: MultiSurfaceViewSpec;
  patches: GranularSurfacePatch[];
  invalidatedPhases: string[];
}

interface PhaseCacheRecord {
  rolesHash: string;
  hierarchyHash: string;
  interactionsHash: string;
  densityHash: string;
  contextHash: string;
  // Phase output caches
  caps: ReturnType<typeof resolveCapabilities>;
  widgets: WidgetInstance[];
  layoutNodes: LayoutNode[];
  adapters: AdapterBinding[];
  events: EventRoute[];
  fixtures: FixtureOverrides;
  viewSpec: ViewSpec;
}

/**
 * Incremental DesignIR Compiler Engine
 *
 * Invariants:
 * 1. Maintains phase-level memoization across all 7 compiler stages per surface.
 * 2. Compares fine-grained dependency hashes (roles, hierarchy, interactions, density, context).
 * 3. Regenerates ONLY invalidated compiler phases on incremental AST edits.
 * 4. Emits granular delta patches for hot reload without tearing down ContractStateStores.
 */
export class IncrementalDesignIRCompiler {
  private surfacePhaseCaches = new Map<string, PhaseCacheRecord>();
  private workflowsCache: { hash: string; table: WorkflowRoutingTable } = {
    hash: "",
    table: {},
  };
  private lastMultiSpec: MultiSurfaceViewSpec | null = null;

  /**
   * Incrementally compiles a DesignIR document with phase-level dependency diffing
   */
  compile(ir: DesignIR): IncrementalCompilationResult {
    const validation = validateDesignIR(ir);
    if (!validation.ok) {
      throw new Error(`[IncrementalCompiler] ${validation.errors?.join("; ")}`);
    }

    const surfaces: Record<string, ViewSpec> = {};
    const patches: GranularSurfacePatch[] = [];
    const invalidatedPhases: string[] = [];

    const surfaceEntries: Array<[string, SurfaceSpec]> = ir.surfaces
      ? Object.entries(ir.surfaces)
      : [
          [
            "main",
            {
              id: "main",
              roles: ir.roles || {},
              hierarchy: ir.hierarchy || {},
              interactions: ir.interactions || [],
              density: ir.density,
              context: ir.context,
            },
          ],
        ];

    const currentSurfaceIds = new Set(surfaceEntries.map(([id]) => id));

    // Detect removed surfaces
    for (const cachedSurfaceId of this.surfacePhaseCaches.keys()) {
      if (!currentSurfaceIds.has(cachedSurfaceId)) {
        this.surfacePhaseCaches.delete(cachedSurfaceId);
        patches.push({
          surfaceId: cachedSurfaceId,
          patchType: "SURFACE_REMOVED",
        });
        invalidatedPhases.push(`surface:${cachedSurfaceId}:removed`);
      }
    }

    // Compile surfaces with phase memoization
    for (const [surfaceId, surface] of surfaceEntries) {
      const cached = this.surfacePhaseCaches.get(surfaceId);

      const rolesHash = deterministicHash(surface.roles || {});
      const hierarchyHash = deterministicHash(surface.hierarchy || {});
      const interactionsHash = deterministicHash(surface.interactions || []);
      const densityHash = surface.density || ir.density || "normal";
      const contextHash = deterministicHash({
        global: ir.globalContext,
        surface: surface.context,
      });

      const effectiveContext = {
        surfaceType: surface.kind || "dashboard",
        timeSensitivity:
          surface.context?.timeSensitivity || ir.globalContext?.timeSensitivity || "nearRealTime",
        reliabilityBias:
          surface.context?.reliabilityBias || ir.globalContext?.reliabilityBias || "strong",
        filters: { ...(ir.globalContext?.filters || {}), ...(surface.context?.filters || {}) },
      };

      if (!cached) {
        // Full surface compilation on cold cache / newly added surface
        const viewSpec = compileSurfaceSpec(surfaceId, surface, ir.globalContext);
        const caps = resolveCapabilities(surface.roles);

        this.surfacePhaseCaches.set(surfaceId, {
          rolesHash,
          hierarchyHash,
          interactionsHash,
          densityHash,
          contextHash,
          caps,
          widgets: viewSpec.widgets,
          layoutNodes: viewSpec.layout.nodes,
          adapters: viewSpec.adapters,
          events: viewSpec.events,
          fixtures: viewSpec.fixtures || {},
          viewSpec,
        });

        surfaces[surfaceId] = viewSpec;
        patches.push({
          surfaceId,
          patchType: "SURFACE_ADDED",
          layout: viewSpec.layout.nodes,
          widgets: viewSpec.widgets,
          adapters: viewSpec.adapters,
          events: viewSpec.events,
          fixtures: viewSpec.fixtures,
        });
        invalidatedPhases.push(`surface:${surfaceId}:full`);
        continue;
      }

      // Check granular phase invalidation triggers
      const rolesChanged = cached.rolesHash !== rolesHash;
      const hierarchyChanged = cached.hierarchyHash !== hierarchyHash;
      const interactionsChanged = cached.interactionsHash !== interactionsHash;
      const densityChanged = cached.densityHash !== densityHash;
      const contextChanged = cached.contextHash !== contextHash;

      let caps = cached.caps;
      let widgets = cached.widgets;
      let layoutNodes = cached.layoutNodes;
      let adapters = cached.adapters;
      let events = cached.events;
      let fixtures = cached.fixtures;

      let patchEmitted = false;

      // Phase 2, 3, 5, 7 Invalidation (Roles / Density / Context changes)
      if (rolesChanged || densityChanged || contextChanged) {
        caps = resolveCapabilities(surface.roles);
        widgets = selectWidgets(caps, surface.roles, densityHash, effectiveContext);
        adapters = bindAdapters(
          widgets,
          surface.roles,
          surface.interactions,
          densityHash,
          effectiveContext,
        );
        fixtures = synthesizeFixtures(caps);

        patches.push({
          surfaceId,
          patchType: "WIDGETS_AND_ADAPTERS",
          widgets,
          adapters,
          fixtures,
        });
        invalidatedPhases.push(`surface:${surfaceId}:phase2_3_5_7`);
        patchEmitted = true;
      }

      // Phase 4 Invalidation (Hierarchy / Roles / Density changes)
      if (hierarchyChanged || rolesChanged || densityChanged) {
        const synthesized = synthesizeLayout(
          widgets,
          surface.hierarchy,
          surface.roles,
          densityHash,
        );
        layoutNodes = synthesized.nodes;

        if (!patchEmitted) {
          patches.push({
            surfaceId,
            patchType: "LAYOUT_ONLY",
            layout: layoutNodes,
          });
          invalidatedPhases.push(`surface:${surfaceId}:phase4_layout`);
          patchEmitted = true;
        }
      }

      // Phase 6 Invalidation (Interactions changes)
      if (interactionsChanged || rolesChanged) {
        events = synthesizeEventRouting(surface.interactions, widgets);

        if (!patchEmitted) {
          patches.push({
            surfaceId,
            patchType: "EVENTS_ONLY",
            events,
          });
          invalidatedPhases.push(`surface:${surfaceId}:phase6_events`);
        }
      }

      const updatedViewSpec: ViewSpec = {
        id: `viewspec-${surfaceId}`,
        name: surface.name || surfaceId,
        description: surface.description,
        surfaceId,
        layout: {
          nodes: layoutNodes,
          hierarchy: surface.hierarchy,
          density: densityHash,
        },
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

      // Save updated phase cache
      this.surfacePhaseCaches.set(surfaceId, {
        rolesHash,
        hierarchyHash,
        interactionsHash,
        densityHash,
        contextHash,
        caps,
        widgets,
        layoutNodes,
        adapters,
        events,
        fixtures,
        viewSpec: updatedViewSpec,
      });

      surfaces[surfaceId] = updatedViewSpec;
    }

    // Workflows Invalidation Check
    const currentWorkflowsHash = deterministicHash(ir.workflows || []);
    let workflowsTable = this.workflowsCache.table;
    if (this.workflowsCache.hash !== currentWorkflowsHash) {
      workflowsTable = synthesizeWorkflows(ir.workflows, ir.surfaces);
      this.workflowsCache = {
        hash: currentWorkflowsHash,
        table: workflowsTable,
      };
      invalidatedPhases.push("workflows");
    }

    const multiSpec: MultiSurfaceViewSpec = {
      id: ir.id || `multisurface-${deterministicHash(ir)}`,
      name: ir.name,
      description: ir.description,
      surfaces,
      globalContext: ir.globalContext as Record<string, unknown>,
      workflows: workflowsTable,
      activeSurfaceId: Object.keys(surfaces)[0],
    };

    this.lastMultiSpec = multiSpec;

    return {
      spec: multiSpec,
      patches,
      invalidatedPhases,
    };
  }

  clear(): void {
    this.surfacePhaseCaches.clear();
    this.workflowsCache = { hash: "", table: {} };
    this.lastMultiSpec = null;
  }
}
