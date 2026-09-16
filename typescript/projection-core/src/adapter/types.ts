import { CapabilityId } from "../types/designIR";
import { PayloadSource } from "../types/viewSpec";

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
  args?: Record<string, any>;
}

export interface Adapter {
  id: string;
  source: PayloadSource;
  steps: TransformStep[];
  outputContract: CapabilityId;
}
