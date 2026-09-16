import { CapabilityRef, ConstraintSet } from "./designIR";

export interface MetricSeriesContract {
  points: Array<{ x: number | string; y: number }>;
  unit?: string;
  trend?: "up" | "down" | "flat";
  threshold?: number;
  status?: "ok" | "warn" | "error";
}

export interface EntityCollectionContract {
  items: Array<Record<string, unknown>>;
  columns: Array<{ key: string; label: string; type?: string }>;
  selection?: string | string[];
  filters?: Record<string, unknown>;
  sort?: { key: string; direction: "asc" | "desc" };
}

export interface StatusBoardContract {
  stages: Array<{ id: string; label: string }>;
  items: Array<{
    id: string;
    stage: string;
    title: string;
    description?: string;
    priority?: "low" | "medium" | "high";
  }>;
}

export interface TimelineContract {
  events: Array<{
    id: string;
    timestamp: string | number;
    type: string;
    message: string;
    metadata?: Record<string, unknown>;
  }>;
  range?: { start: number | string; end: number | string };
}

export interface KeyMetricMatrixContract {
  metrics: Array<{
    id: string;
    label: string;
    value: number | string;
    unit?: string;
    trend?: "up" | "down" | "flat";
    status?: "ok" | "warn" | "error";
  }>;
  groups?: Array<{ id: string; label: string; metricIds: string[] }>;
}

export interface ConsensusMatrixContract {
  items: Array<{
    id: string;
    label: string;
    votes: Array<{
      agent: string;
      value: number;
      confidence: number;
    }>;
  }>;
  resolution?: {
    id: string;
    label: string;
    confidence: number;
  };
}

export interface InspectorPanelContract {
  target?: Record<string, unknown>;
  fields?: Array<{ key: string; label: string; type?: string }>;
}

export interface AuditStreamContract {
  entries: Array<{
    id: string;
    timestamp: number | string;
    actor?: string;
    action: string;
    details?: Record<string, unknown>;
  }>;
}

export interface WorkQueueContract {
  items: Array<{
    id: string;
    type: string;
    status: string;
    createdAt: number | string;
    updatedAt?: number | string;
    metadata?: Record<string, unknown>;
  }>;
}

export interface SurfaceContextContract {
  surfaceType: "dashboard" | "workbench" | "inspector" | "timelineView";
  timeSensitivity: "realTime" | "nearRealTime" | "batch" | "historical";
  reliabilityBias: "eventual" | "strong" | "strict";
}

export type CapabilityContract =
  | MetricSeriesContract
  | EntityCollectionContract
  | StatusBoardContract
  | TimelineContract
  | KeyMetricMatrixContract
  | ConsensusMatrixContract
  | InspectorPanelContract
  | AuditStreamContract
  | WorkQueueContract
  | SurfaceContextContract;

export interface ResolvedCapability {
  role: string;
  contract: CapabilityContract;
  capabilityRef: CapabilityRef;
  constraints?: ConstraintSet;
}
