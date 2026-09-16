import { DesignIR, CapabilityId, ContextSpec, InteractionSpec, HierarchySetting, LayoutBias, DensitySetting } from "../types/designIR";
import { ViewSpec, LayoutNode, WidgetInstance, AdapterBinding, EventRoute, FixtureOverrides, ViewSpecAction } from "../types/viewSpec";
import { ResolvedCapability, SurfaceContextContract } from "../types/capabilities";
import { WidgetCatalog } from "../widget/catalog";

let _id = 0;
function nanoid(_size?: number): string {
  return `${Date.now().toString(36)}-${(++_id).toString(36)}`;
}

export class DesignIRCompiler {
  private widgetCatalog: WidgetCatalog;

  constructor() {
    this.widgetCatalog = new WidgetCatalog();
  }

  compileDesignIR(ir: DesignIR): ViewSpec {
    const ast = this.parseAndValidate(ir);
    const caps = this.resolveCapabilities(ast);
    const widgets = this.selectWidgets(caps, ast);
    const layout = this.synthesizeLayout(widgets, ast);
    const adapters = this.bindAdapters(widgets, ast);
    const events = this.synthesizeEventRouting(widgets, ast);

    return {
      id: nanoid(),
      name: ir.name,
      layout,
      widgets,
      adapters,
      events,
      context: ast.context ? this.buildSurfaceContext(ast.context) : undefined,
      fixtures: this.generateFixtures(widgets, ast),
    };
  }

  private parseAndValidate(ir: DesignIR): DesignIR {
    for (const [roleName, role] of Object.entries(ir.roles)) {
      if (!role.capability) {
        throw new Error(`Role "${roleName}" missing capability`);
      }
      if (!role.capability.id) {
        throw new Error(`Role "${roleName}" has invalid capability`);
      }
    }

    for (const interaction of ir.interactions) {
      if (interaction.sourceRole && !ir.roles[interaction.sourceRole]) {
        throw new Error(`Interaction source role "${interaction.sourceRole}" not found`);
      }
      if (interaction.targetRole && !ir.roles[interaction.targetRole]) {
        throw new Error(`Interaction target role "${interaction.targetRole}" not found`);
      }
    }

    if (ir.hierarchy) {
      const allRoles = Object.keys(ir.roles);
      const hierarchies = [
        ...(ir.hierarchy.primaryRoles || []),
        ...(ir.hierarchy.secondaryRoles || []),
        ...(ir.hierarchy.ambientRoles || []),
      ];
      for (const role of hierarchies) {
        if (!allRoles.includes(role)) {
          console.warn(`Hierarchy references unknown role: ${role}`);
        }
      }
    }

    return ir;
  }

  private resolveCapabilities(ast: DesignIR): ResolvedCapability[] {
    const resolved: ResolvedCapability[] = [];

    for (const [roleName, roleSpec] of Object.entries(ast.roles)) {
      const contract = this.createContractStub(roleSpec.capability.id as CapabilityId);

      resolved.push({
        role: roleName,
        contract,
        capabilityRef: roleSpec.capability,
        constraints: roleSpec.constraints,
      });
    }

    return resolved;
  }

  private createContractStub(capabilityId: CapabilityId): any {
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
        return {};
      default:
        return {};
    }
  }

  private selectWidgets(caps: ResolvedCapability[], ast: DesignIR): WidgetInstance[] {
    const instances: WidgetInstance[] = [];

    for (const cap of caps) {
      const roleSpec = ast.roles[cap.role];
      const density: DensitySetting = roleSpec!.density || ast.density || "normal";

      const widgetDef = this.widgetCatalog.selectWidget(
        cap.capabilityRef.id,
        cap.capabilityRef.variant,
        density,
      );

      if (!widgetDef) {
        throw new Error(`No widget found for capability: ${cap.capabilityRef.id}`);
      }

      instances.push({
        id: nanoid(),
        contract: cap.contract,
        widget: {
          id: widgetDef.id,
          type: widgetDef.id,
          contract: cap.capabilityRef.id,
          props: {
            density,
            priority: roleSpec!.priority || "secondary",
          },
        },
        role: cap.role,
      });
    }

    return instances;
  }

  private synthesizeLayout(
    widgets: WidgetInstance[],
    ast: DesignIR,
  ): { nodes: LayoutNode[]; hierarchy?: HierarchySetting; density?: DensitySetting } {
    const nodes: LayoutNode[] = [];
    const hierarchy = ast.hierarchy || {
      primaryRoles: [],
      secondaryRoles: [],
      ambientRoles: [],
    };

    const priorityMap: Record<string, number> = {};
    for (const role of hierarchy.primaryRoles || []) {
      priorityMap[role] = 0;
    }
    for (const role of hierarchy.secondaryRoles || []) {
      priorityMap[role] = 1;
    }
    for (const role of hierarchy.ambientRoles || []) {
      priorityMap[role] = 2;
    }

    for (const widget of widgets) {
      if (!widget.role) continue;
      if (!(widget.role in priorityMap)) {
        const roleSpec = ast.roles[widget.role];
        if (roleSpec?.priority === "primary") {
          priorityMap[widget.role] = 0;
        } else if (roleSpec?.priority === "secondary") {
          priorityMap[widget.role] = 1;
        } else {
          priorityMap[widget.role] = 2;
        }
      }
    }

    const sortedWidgets = [...widgets].sort((a, b) => {
      const aPriority = a.role ? (priorityMap[a.role] ?? 2) : 2;
      const bPriority = b.role ? (priorityMap[b.role] ?? 2) : 2;
      return aPriority - bPriority;
    });

    for (const widget of sortedWidgets) {
      const roleSpec = widget.role ? ast.roles[widget.role] : undefined;
      const layoutBias = roleSpec?.constraints?.layoutBias ?? ("main" as LayoutBias);
      const priority = widget.role
        ? priorityMap[widget.role] !== undefined
          ? (["primary", "secondary", "ambient"][priorityMap[widget.role]!] as any)
          : "secondary"
        : "secondary";

      nodes.push({
        id: nanoid(),
        widgetId: widget.id,
        region: this.mapLayoutBias(layoutBias),
        layout: {
          order: nodes.length,
          flex: priority === "primary" ? 2 : priority === "ambient" ? 0.5 : 1,
        },
        priority,
      });
    }

    return {
      nodes,
      hierarchy,
      density: ast.density,
    };
  }

  private mapLayoutBias(bias: LayoutBias): LayoutNode["region"] {
    const map: Record<LayoutBias, LayoutNode["region"]> = {
      main: "main",
      sidebar: "sidebar",
      footer: "footer",
      header: "header",
      overlay: "overlay",
    };
    return map[bias] || "main";
  }

  private bindAdapters(widgets: WidgetInstance[], _ast: DesignIR): AdapterBinding[] {
    const bindings: AdapterBinding[] = [];

    for (const widget of widgets) {
      bindings.push({
        widgetId: widget.id,
        adapterId: `${widget.widget.contract}_adapter_${nanoid(6)}`,
        source: {
          type: "mock",
          mock: this.generateMockData(widget.widget.contract),
        },
        outputContract: widget.widget.contract as CapabilityId,
      });
    }

    return bindings;
  }

  private synthesizeEventRouting(widgets: WidgetInstance[], ast: DesignIR): EventRoute[] {
    const routes: EventRoute[] = [];

    for (const interaction of ast.interactions) {
      const sourceRole = interaction.sourceRole;
      if (!sourceRole) continue;

      const sourceWidget = widgets.find((w) => w.role === sourceRole);
      if (!sourceWidget) continue;

      const action = this.mapInteractionToAction(interaction, widgets);
      if (!action) continue;

      routes.push({
        fromWidget: sourceWidget.id,
        event: interaction.verb,
        action,
      });
    }

    return routes;
  }

  private mapInteractionToAction(
    interaction: InteractionSpec,
    widgets: WidgetInstance[],
  ): ViewSpecAction | undefined {
    const targetRole = interaction.targetRole;
    const targetWidget = targetRole
      ? widgets.find((w) => w.role === targetRole)
      : undefined;

    switch (interaction.verb) {
      case "navigate":
        return { type: "navigate", target: targetRole || "/" };
      case "inspect":
        return { type: "inspect", widgetId: targetWidget?.id || "" };
      case "drilldown":
        return { type: "drilldown", widgetId: targetWidget?.id || "" };
      case "filter":
        return { type: "filter", widgetId: targetWidget?.id || "", filter: {} };
      case "sort":
        return {
          type: "sort",
          widgetId: targetWidget?.id || "",
          sort: { key: "", direction: "asc" },
        };
      case "acknowledge":
        return { type: "acknowledge", widgetId: targetWidget?.id || "" };
      case "dismiss":
        return { type: "dismiss", widgetId: targetWidget?.id || "" };
      case "compare":
        return { type: "compare", widgetId: targetWidget?.id || "", targetId: "" };
      default:
        return undefined;
    }
  }

  private generateFixtures(widgets: WidgetInstance[], _ast: DesignIR): FixtureOverrides {
    const fixtures: FixtureOverrides = {};

    for (const widget of widgets) {
      fixtures[widget.id] = {
        data: this.generateMockData(widget.widget.contract),
        scenario: "nominal",
      };
    }

    return fixtures;
  }

  private generateMockData(capability: CapabilityId): any {
    const timestamp = Date.now();
    const seed = timestamp % 97;

    switch (capability) {
      case "MetricSeries":
        return {
          points: Array.from({ length: 20 }, (_, i) => ({
            x: timestamp - (19 - i) * 60000,
            y: (i * 37 + seed) % 100,
          })),
          unit: "ms",
          status: seed % 5 === 0 ? "warn" : "ok",
        };
      case "EntityCollection":
        return {
          items: Array.from({ length: 10 }, (_, i) => ({
            id: `item_${i}`,
            name: `Item ${i}`,
            status: ["active", "pending", "completed"][i % 3],
            value: (i * 113 + seed) % 1000,
          })),
          columns: [
            { key: "id", label: "ID" },
            { key: "name", label: "Name" },
            { key: "status", label: "Status" },
            { key: "value", label: "Value" },
          ],
        };
      case "StatusBoard":
        return {
          stages: [
            { id: "todo", label: "To Do" },
            { id: "in_progress", label: "In Progress" },
            { id: "done", label: "Done" },
          ],
          items: Array.from({ length: 15 }, (_, i) => ({
            id: `task_${i}`,
            stage: ["todo", "in_progress", "done"][i % 3],
            title: `Task ${i}`,
            priority: ["low", "medium", "high"][i % 3],
          })),
        };
      case "Timeline":
        return {
          events: Array.from({ length: 12 }, (_, i) => ({
            id: `event_${i}`,
            timestamp: timestamp - (11 - i) * 300000,
            type: ["info", "warning", "error"][i % 3],
            message: `Event ${i} occurred`,
          })),
        };
      case "KeyMetricMatrix":
        return {
          metrics: [
            { id: "m1", label: "Requests", value: (1 * 137 + seed) % 1000, status: "ok" },
            { id: "m2", label: "Errors", value: (2 * 47 + seed) % 50, status: seed % 7 === 0 ? "warn" : "ok" },
            { id: "m3", label: "Latency", value: (3 * 61 + seed) % 200, unit: "ms", status: "ok" },
            { id: "m4", label: "Throughput", value: (4 * 103 + seed) % 5000, unit: "req/s", status: "ok" },
          ],
        };
      default:
        return {};
    }
  }

  private buildSurfaceContext(context: ContextSpec): SurfaceContextContract {
    return {
      surfaceType: context.surfaceType || "dashboard",
      timeSensitivity: context.timeSensitivity || "nearRealTime",
      reliabilityBias: context.reliabilityBias || "strong",
    };
  }
}
