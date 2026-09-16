import { CapabilityId, DensitySetting, LayoutBias } from "../types/designIR";

export interface WidgetDefinition {
  id: string;
  name: string;
  implements: CapabilityId[];
  variants?: string[];
  defaultDensity?: DensitySetting;
  defaultLayout?: LayoutBias;
}

export class WidgetCatalog {
  private widgets = new Map<string, WidgetDefinition>();

  constructor() {
    this.registerDefaults();
  }

  private registerDefaults(): void {
    this.widgets.set("Sparkline", {
      id: "Sparkline",
      name: "Sparkline Chart",
      implements: ["MetricSeries"],
      variants: ["line", "area"],
      defaultDensity: "compact",
      defaultLayout: "main",
    });

    this.widgets.set("AreaChart", {
      id: "AreaChart",
      name: "Area Chart",
      implements: ["MetricSeries"],
      variants: ["stacked", "stream"],
      defaultDensity: "normal",
      defaultLayout: "main",
    });

    this.widgets.set("BarSeries", {
      id: "BarSeries",
      name: "Bar Chart",
      implements: ["MetricSeries"],
      variants: ["vertical", "horizontal"],
      defaultDensity: "normal",
      defaultLayout: "main",
    });

    this.widgets.set("KanbanBoard", {
      id: "KanbanBoard",
      name: "Kanban Board",
      implements: ["StatusBoard"],
      defaultDensity: "spacious",
      defaultLayout: "main",
    });

    this.widgets.set("TimelineStrip", {
      id: "TimelineStrip",
      name: "Timeline Strip",
      implements: ["Timeline"],
      defaultDensity: "compact",
      defaultLayout: "main",
    });

    this.widgets.set("DataTable", {
      id: "DataTable",
      name: "Data Table",
      implements: ["EntityCollection"],
      defaultDensity: "compact",
      defaultLayout: "main",
    });

    this.widgets.set("KPIGrid", {
      id: "KPIGrid",
      name: "KPI Grid",
      implements: ["KeyMetricMatrix"],
      defaultDensity: "highSalience",
      defaultLayout: "header",
    });

    this.widgets.set("InspectorPanel", {
      id: "InspectorPanel",
      name: "Inspector Panel",
      implements: ["InspectorPanel"],
      defaultDensity: "normal",
      defaultLayout: "sidebar",
    });

    this.widgets.set("AuditLog", {
      id: "AuditLog",
      name: "Audit Log",
      implements: ["AuditStream"],
      defaultDensity: "compact",
      defaultLayout: "footer",
    });

    this.widgets.set("QueueList", {
      id: "QueueList",
      name: "Queue List",
      implements: ["WorkQueue"],
      defaultDensity: "compact",
      defaultLayout: "main",
    });

    this.widgets.set("ConsensusBoard", {
      id: "ConsensusBoard",
      name: "Consensus Board",
      implements: ["ConsensusMatrix"],
      defaultDensity: "normal",
      defaultLayout: "main",
    });
  }

  getWidget(id: string): WidgetDefinition | undefined {
    return this.widgets.get(id);
  }

  selectWidget(
    capability: CapabilityId,
    variant?: string,
    density?: DensitySetting,
  ): WidgetDefinition | undefined {
    const candidates = Array.from(this.widgets.values()).filter((w) =>
      w.implements.includes(capability),
    );

    if (candidates.length === 0) return undefined;

    if (variant) {
      const withVariant = candidates.find((w) => w.variants?.includes(variant));
      if (withVariant) return withVariant;
    }

    return candidates.sort((a, b) => {
      const aScore = a.defaultDensity === density ? 0 : 1;
      const bScore = b.defaultDensity === density ? 0 : 1;
      return aScore - bScore;
    })[0];
  }
}
