import { Adapter, AdapterOp, TransformStep } from "@/core/adapter/types";
import { CapabilityId } from "@/core/types/designIR";

export interface EndpointPreset {
  name: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  samplePayload: Record<string, unknown>;
  suggestedProjections: Array<{
    targetContract: CapabilityId;
    adapterName: string;
    description: string;
    steps: TransformStep[];
  }>;
}

export const ENDPOINT_PRESETS: EndpointPreset[] = [
  {
    name: "Nebula Execution State & Leases",
    method: "GET",
    path: "/api/execution/state",
    samplePayload: {
      timestamp: "2026-08-20T18:00:00Z",
      requests: { DRAFT: 3, COMPILED: 5, ADMITTED: 2, READY: 1 },
      leases: { ACTIVE: 1, RELEASED: 4 },
      attempts: { RUNNING: 1, SUCCEEDED: 3, FAILED: 1 },
      summary: {
        totalRequests: 11,
        activeLeases: 1,
        systemHealth: "OK",
        p99LatencyMs: 142,
      },
    },
    suggestedProjections: [
      {
        targetContract: "KeyMetricMatrix",
        adapterName: "execution-summary-kpi",
        description:
          "Projects execution totals, active leases, latency and health into KPI summary tiles.",
        steps: [
          { op: "select", args: { path: "summary" } },
          {
            op: "default",
            args: {
              value: {
                metrics: [
                  { id: "total", label: "Total Requests", value: 11, unit: "reqs", status: "ok" },
                  { id: "leases", label: "Active Leases", value: 1, unit: "worker", status: "ok" },
                  { id: "latency", label: "P99 Latency", value: 142, unit: "ms", status: "ok" },
                ],
              },
            },
          },
        ],
      },
      {
        targetContract: "StatusBoard",
        adapterName: "execution-requests-board",
        description: "Projects request pipeline stage counts into a multi-stage status board.",
        steps: [
          { op: "select", args: { path: "requests" } },
          {
            op: "default",
            args: {
              stages: [
                { id: "DRAFT", label: "Draft & Intake" },
                { id: "COMPILED", label: "Compiled AST" },
                { id: "ADMITTED", label: "Admitted" },
                { id: "READY", label: "Ready" },
              ],
              items: [
                { id: "REQ-01", stage: "DRAFT", title: "Intake validation rule", priority: "low" },
                {
                  id: "REQ-02",
                  stage: "COMPILED",
                  title: "AST verification pass",
                  priority: "high",
                },
                {
                  id: "REQ-03",
                  stage: "READY",
                  title: "Worker lease allocation",
                  priority: "medium",
                },
              ],
            },
          },
        ],
      },
    ],
  },
  {
    name: "Nebula Bitemporal Spec Revisions",
    method: "GET",
    path: "/api/specifications",
    samplePayload: {
      specId: "SPEC-084",
      title: "Bitemporal Spec Revision Diff",
      revisions: [
        {
          revisionNumber: 3,
          revisionType: "MAJOR_REFACTOR",
          changeSummary:
            "Migrated plan status storage from conduit.plan_status to nebula.plans schema.",
          validFrom: "2026-08-20T00:00:00Z",
          validUntil: null,
          derivedFrom: ["AGENDA-012"],
          isCurrent: true,
        },
        {
          revisionNumber: 2,
          revisionType: "AMENDMENT",
          changeSummary:
            "Added optimistic concurrency check during requirement kanban move transition.",
          validFrom: "2026-08-14T12:00:00Z",
          validUntil: "2026-08-20T00:00:00Z",
          derivedFrom: ["OQ-441"],
          isCurrent: false,
        },
        {
          revisionNumber: 1,
          revisionType: "INITIAL_BASELINE",
          changeSummary: "Initial schema definition for two-stage requirement compilation.",
          validFrom: "2026-08-01T08:30:00Z",
          validUntil: "2026-08-14T12:00:00Z",
          derivedFrom: ["HARVEST-009"],
          isCurrent: false,
        },
      ],
    },
    suggestedProjections: [
      {
        targetContract: "Timeline",
        adapterName: "spec-revisions-timeline",
        description: "Projects specification revisions into a bitemporal timeline stream.",
        steps: [
          { op: "select", args: { path: "revisions" } },
          {
            op: "map",
            args: {
              fields: {
                id: "revisionNumber",
                type: "revisionType",
                message: "changeSummary",
                timestamp: "validFrom",
              },
            },
          },
          { op: "sortBy", args: { key: "id", direction: "desc" } },
        ],
      },
      {
        targetContract: "EntityCollection",
        adapterName: "spec-revisions-table",
        description: "Projects revision list into tabular rows with columns.",
        steps: [
          { op: "select", args: { path: "revisions" } },
          {
            op: "map",
            args: {
              fields: {
                id: "revisionNumber",
                type: "revisionType",
                summary: "changeSummary",
                validFrom: "validFrom",
              },
            },
          },
        ],
      },
    ],
  },
  {
    name: "System Metric Telemetry Stream",
    method: "GET",
    path: "/api/metrics/sparkline",
    samplePayload: {
      metrics: {
        health: {
          label: "P99 Response Latency",
          unit: "ms",
          samples: [
            { timestamp: 1724180000000, value: 45, status: "OK" },
            { timestamp: 1724180060000, value: 67, status: "OK" },
            { timestamp: 1724180120000, value: 82, status: "WARN" },
            { timestamp: 1724180180000, value: 55, status: "OK" },
            { timestamp: 1724180240000, value: 49, status: "OK" },
          ],
        },
      },
    },
    suggestedProjections: [
      {
        targetContract: "MetricSeries",
        adapterName: "metric-series-sparkline",
        description:
          "Extracts telemetry samples, maps {x,y} coordinates, sorts ascending, and normalizes status enums.",
        steps: [
          { op: "select", args: { path: "metrics.health.samples" } },
          {
            op: "map",
            args: {
              fields: {
                x: "timestamp",
                y: "value",
              },
            },
          },
          { op: "sortBy", args: { key: "x", direction: "asc" } },
        ],
      },
    ],
  },
];

export const AVAILABLE_OPS: Array<{
  op: AdapterOp;
  label: string;
  category: "Extract" | "Transform" | "Aggregate" | "Semantic";
  description: string;
  defaultArgs: Record<string, unknown>;
}> = [
  {
    op: "select",
    label: "Select (JSONPath)",
    category: "Extract",
    description: "Drill down into a nested payload object via dot-notation path.",
    defaultArgs: { path: "data.items" },
  },
  {
    op: "pluck",
    label: "Pluck Field",
    category: "Extract",
    description:
      "Extract an array of scalar values corresponding to a single key across all objects.",
    defaultArgs: { key: "id" },
  },
  {
    op: "map",
    label: "Map Field Projections",
    category: "Transform",
    description: "Project domain fields into target contract schema keys.",
    defaultArgs: { fields: { x: "timestamp", y: "value", label: "name" } },
  },
  {
    op: "filter",
    label: "Filter Criteria",
    category: "Transform",
    description: "Keep only elements matching exact criteria or regular expressions.",
    defaultArgs: { where: { status: "OK" } },
  },
  {
    op: "distinct",
    label: "Distinct / Deduplicate",
    category: "Transform",
    description: "Filter out duplicate records based on a unique identifier field.",
    defaultArgs: { key: "id" },
  },
  {
    op: "sortBy",
    label: "Sort By",
    category: "Transform",
    description: "Order array records in ascending or descending sequence.",
    defaultArgs: { key: "timestamp", direction: "asc" },
  },
  {
    op: "groupBy",
    label: "Group By",
    category: "Aggregate",
    description: "Partition an array into categorical buckets keyed by a discriminator field.",
    defaultArgs: { key: "stage" },
  },
  {
    op: "count",
    label: "Count",
    category: "Aggregate",
    description: "Count total array elements and emit as scalar integer.",
    defaultArgs: {},
  },
  {
    op: "countBy",
    label: "Count By Category",
    category: "Aggregate",
    description: "Generate a frequency distribution map of occurrence counts per field value.",
    defaultArgs: { key: "status" },
  },
  {
    op: "flatten",
    label: "Flatten Nested Arrays",
    category: "Transform",
    description: "Flatten one level of nested arrays into a contiguous sequence.",
    defaultArgs: {},
  },
  {
    op: "coalesce",
    label: "Coalesce Fallbacks",
    category: "Transform",
    description: "Select the first non-null, non-empty value among candidates.",
    defaultArgs: { fields: ["displayName", "title", "name", "id"] },
  },
  {
    op: "default",
    label: "Default Fallback Value",
    category: "Transform",
    description: "Provide a safe fallback if the current pipeline value is null or undefined.",
    defaultArgs: { value: [] },
  },
  {
    op: "format",
    label: "Format Value",
    category: "Transform",
    description: "Format date/time, numeric currency, percent, or compact metrics.",
    defaultArgs: { type: "number", format: "compact" },
  },
  {
    op: "semanticMap",
    label: "Semantic Enum Mapping",
    category: "Semantic",
    description:
      "Map domain status codes or states to standardized contract status flags (e.g. FAILED -> error).",
    defaultArgs: {
      field: "status",
      map: {
        OK: "ok",
        RUNNING: "ok",
        WARN: "warn",
        PENDING: "warn",
        FAILED: "error",
        ERROR: "error",
      },
    },
  },
];
