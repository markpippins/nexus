import { CapabilityId } from "../types/designIR";
import { PayloadSource } from "../types/viewSpec";

/**
 * Adapter Operations
 * Declarative transformation operators that project arbitrary payload structures
 * (REST payloads, WebSocket/SSE streams, GraphQL queries, telemetry arrays)
 * into strictly typed Capability Contracts.
 */
export type AdapterOp =
  | "select"
  | "pluck"
  | "filter"
  | "distinct"
  | "map"
  | "groupBy"
  | "count"
  | "countBy"
  | "sortBy"
  | "flatten"
  | "coalesce"
  | "default"
  | "format"
  | "semanticMap";

export interface TransformStep {
  op: AdapterOp;
  args?: Record<string, unknown>;
}

/**
 * Adapter Projection Specification
 * Canonical Invariant: Adapters are payload → capability contract projections,
 * NOT REST → widget bindings. Widgets bind strictly to Capability Contracts;
 * Adapters are the decoupled bridges that project raw payloads into contracts.
 */
export interface Adapter {
  id: string;
  source: PayloadSource;
  steps: TransformStep[];
  outputContract: CapabilityId;
}
