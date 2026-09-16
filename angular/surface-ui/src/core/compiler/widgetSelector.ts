import {
  CapabilityId,
  DensitySetting,
  LayoutBias,
  RoleSpec,
  ContextSpec,
  TimeSensitivity,
  ReliabilityBias,
  SurfaceContextSpec,
} from "../types/designIR";

export interface WidgetCatalogEntry {
  id: string;
  name: string;
  capabilityId: CapabilityId;
  variant: string;
  density: DensitySetting[];
  regions: LayoutBias[];
  salience: "low" | "medium" | "high";
  contextBias?: {
    timeSensitivity?: TimeSensitivity;
    reliabilityBias?: ReliabilityBias;
  };
}

export const CANONICAL_WIDGET_CATALOG: WidgetCatalogEntry[] = [
  {
    id: "Sparkline",
    name: "Sparkline Chart",
    capabilityId: "MetricSeries",
    variant: "line",
    density: ["compact", "normal"],
    regions: ["main", "sidebar", "header"],
    salience: "medium",
    contextBias: {
      timeSensitivity: "realTime",
      reliabilityBias: "eventual",
    },
  },
  {
    id: "AreaChart",
    name: "Area Chart",
    capabilityId: "MetricSeries",
    variant: "area",
    density: ["normal", "spacious", "highSalience"],
    regions: ["main"],
    salience: "high",
    contextBias: {
      timeSensitivity: "nearRealTime",
      reliabilityBias: "strong",
    },
  },
  {
    id: "BarSeries",
    name: "Bar Chart",
    capabilityId: "MetricSeries",
    variant: "bar",
    density: ["normal", "spacious"],
    regions: ["main"],
    salience: "medium",
    contextBias: {
      timeSensitivity: "historical",
      reliabilityBias: "strong",
    },
  },
  {
    id: "DataTable",
    name: "Data Table",
    capabilityId: "EntityCollection",
    variant: "table",
    density: ["compact", "normal", "spacious"],
    regions: ["main", "sidebar"],
    salience: "high",
    contextBias: {
      timeSensitivity: "historical",
      reliabilityBias: "strict",
    },
  },
  {
    id: "CardGrid",
    name: "Entity Card Grid",
    capabilityId: "EntityCollection",
    variant: "card-grid",
    density: ["normal", "spacious", "highSalience"],
    regions: ["main"],
    salience: "high",
    contextBias: {
      timeSensitivity: "nearRealTime",
      reliabilityBias: "strong",
    },
  },
  {
    id: "KanbanBoard",
    name: "Kanban Board",
    capabilityId: "StatusBoard",
    variant: "kanban",
    density: ["normal", "spacious", "highSalience"],
    regions: ["main"],
    salience: "high",
    contextBias: {
      timeSensitivity: "nearRealTime",
      reliabilityBias: "strong",
    },
  },
  {
    id: "TimelineStrip",
    name: "Timeline Strip",
    capabilityId: "Timeline",
    variant: "vertical-stream",
    density: ["compact", "normal"],
    regions: ["main", "sidebar"],
    salience: "medium",
    contextBias: {
      timeSensitivity: "historical",
      reliabilityBias: "strict",
    },
  },
  {
    id: "KPIGrid",
    name: "KPI Matrix Grid",
    capabilityId: "KeyMetricMatrix",
    variant: "header-strip",
    density: ["compact", "normal", "highSalience"],
    regions: ["header", "main"],
    salience: "high",
    contextBias: {
      timeSensitivity: "realTime",
      reliabilityBias: "strong",
    },
  },
  {
    id: "InspectorPanel",
    name: "Inspector Panel",
    capabilityId: "InspectorPanel",
    variant: "sidebar-drawer",
    density: ["compact", "normal", "spacious"],
    regions: ["sidebar", "overlay", "footer"],
    salience: "medium",
    contextBias: {
      timeSensitivity: "nearRealTime",
      reliabilityBias: "strong",
    },
  },
  {
    id: "AuditLog",
    name: "Audit Stream Log",
    capabilityId: "AuditStream",
    variant: "table-log",
    density: ["compact", "normal"],
    regions: ["footer", "sidebar", "main"],
    salience: "low",
    contextBias: {
      timeSensitivity: "realTime",
      reliabilityBias: "strict",
    },
  },
  {
    id: "QueueList",
    name: "Work Queue List",
    capabilityId: "WorkQueue",
    variant: "pipeline-console",
    density: ["compact", "normal"],
    regions: ["main", "sidebar"],
    salience: "medium",
    contextBias: {
      timeSensitivity: "realTime",
      reliabilityBias: "strong",
    },
  },
  {
    id: "ConsensusBoard",
    name: "Consensus Deliberation Board",
    capabilityId: "ConsensusMatrix",
    variant: "vote-grid",
    density: ["normal", "spacious", "highSalience"],
    regions: ["main"],
    salience: "high",
    contextBias: {
      timeSensitivity: "nearRealTime",
      reliabilityBias: "strict",
    },
  },
];

export interface SelectionScoreBreakdown {
  entryId: string;
  totalScore: number;
  salienceScore: number;
  regionScore: number;
  timeSensitivityScore: number;
  reliabilityScore: number;
  variantMatch: boolean;
}

/**
 * Deterministic Widget Selector
 *
 * Rules:
 * 1. Hard filter by capabilityId (must match contract).
 * 2. Hard filter by explicit variant override (role.constraints.widgetVariant or role.capability.variant).
 * 3. Density filter / fallback.
 * 4. Numeric pure scoring by:
 *    - Salience alignment to role priority (primary -> high, secondary -> medium, ambient -> low)
 *    - Region alignment (matches role.constraints.layoutBias or default regional preference)
 *    - Context bias alignment (timeSensitivity and reliabilityBias)
 * 5. Deterministic tie-breaker: sort by score DESC, then entry.id ASC.
 */
export function selectWidgetDeterministically(
  roleName: string,
  role: RoleSpec,
  effectiveDensity: DensitySetting,
  context?: SurfaceContextSpec,
  catalog: WidgetCatalogEntry[] = CANONICAL_WIDGET_CATALOG,
): { selected: WidgetCatalogEntry; breakdown: SelectionScoreBreakdown } {
  const capabilityId = role.capability.id;
  const requestedVariant = role.constraints?.widgetVariant || role.capability.variant;

  // Step 1: Filter by capability match
  let candidates = catalog.filter((entry) => entry.capabilityId === capabilityId);

  if (candidates.length === 0) {
    throw new Error(
      `[WidgetSelector] No catalog entries registered for capability: ${capabilityId}`,
    );
  }

  // Step 2: Filter by explicit variant override if provided
  if (requestedVariant) {
    const variantMatches = candidates.filter((entry) => entry.variant === requestedVariant);
    if (variantMatches.length > 0) {
      candidates = variantMatches;
    }
  }

  // Step 3: Density compatibility check
  const densityMatches = candidates.filter((entry) => entry.density.includes(effectiveDensity));
  if (densityMatches.length > 0) {
    candidates = densityMatches;
  }

  // Target region expectation
  const targetRegion: LayoutBias =
    role.constraints?.layoutBias ||
    (role.priority === "primary" ? "main" : role.priority === "secondary" ? "sidebar" : "footer");

  // Step 4: Pure scoring calculation
  const scored = candidates.map((entry) => {
    let salienceScore = 0;
    let regionScore = 0;
    let timeSensitivityScore = 0;
    let reliabilityScore = 0;

    // Hierarchy & Salience Scoring
    if (role.priority === "primary") {
      salienceScore = entry.salience === "high" ? 30 : entry.salience === "medium" ? 10 : -10;
    } else if (role.priority === "secondary") {
      salienceScore = entry.salience === "medium" ? 30 : 10;
    } else {
      // ambient
      salienceScore = entry.salience === "low" ? 30 : entry.salience === "medium" ? 5 : -20;
    }

    // Region Compatibility Scoring
    if (entry.regions.includes(targetRegion)) {
      regionScore = 20;
    }

    // Context Bias Scoring
    if (context?.timeSensitivity && entry.contextBias?.timeSensitivity) {
      if (context.timeSensitivity === entry.contextBias.timeSensitivity) {
        timeSensitivityScore = 15;
      }
    }

    if (context?.reliabilityBias && entry.contextBias?.reliabilityBias) {
      if (context.reliabilityBias === entry.contextBias.reliabilityBias) {
        reliabilityScore = 15;
      }
    }

    const totalScore = salienceScore + regionScore + timeSensitivityScore + reliabilityScore;

    const breakdown: SelectionScoreBreakdown = {
      entryId: entry.id,
      totalScore,
      salienceScore,
      regionScore,
      timeSensitivityScore,
      reliabilityScore,
      variantMatch: requestedVariant ? entry.variant === requestedVariant : false,
    };

    return { entry, breakdown };
  });

  // Step 5: Deterministic sort: score DESC, then lexicographical entry.id ASC
  scored.sort((a, b) => {
    if (b.breakdown.totalScore !== a.breakdown.totalScore) {
      return b.breakdown.totalScore - a.breakdown.totalScore;
    }
    return a.entry.id.localeCompare(b.entry.id);
  });

  return {
    selected: scored[0].entry,
    breakdown: scored[0].breakdown,
  };
}
