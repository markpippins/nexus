import { WidgetImplementation } from "./types";

export class WidgetRegistry {
  private implementations: Map<string, WidgetImplementation> = new Map();

  register(id: string, implementation: WidgetImplementation): void {
    this.implementations.set(id, implementation);
  }

  get(id: string): WidgetImplementation | undefined {
    return this.implementations.get(id);
  }

  registerDefaults(): void {
    this.register("Sparkline", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget sparkline">Sparkline: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click", "hover"],
    });

    this.register("AreaChart", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget area-chart">Area Chart: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click", "hover", "select"],
    });

    this.register("BarSeries", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget bar-series">Bar Chart: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click", "select"],
    });

    this.register("KanbanBoard", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget kanban">Kanban Board: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["drop", "click", "move"],
    });

    this.register("TimelineStrip", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget timeline">Timeline: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click", "hover", "select"],
    });

    this.register("DataTable", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget data-table">Data Table: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click", "select", "sort", "filter"],
    });

    this.register("KPIGrid", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget kpi-grid">KPI Grid: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click"],
    });

    this.register("InspectorPanel", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget inspector">Inspector: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["dismiss", "navigate"],
    });

    this.register("AuditLog", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget audit-log">Audit Log: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click", "filter"],
    });

    this.register("QueueList", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget queue-list">Queue: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click", "select", "acknowledge"],
    });

    this.register("ConsensusBoard", {
      render: (props, container) => {
        container.innerHTML = `<div class="widget consensus">Consensus: ${JSON.stringify(props.data)}</div>`;
      },
      events: ["click", "compare"],
    });
  }
}
