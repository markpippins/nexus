export type JsonType = "string" | "number" | "boolean" | "null" | "array" | "object";

export type WidgetType =
  "react-component" | "data-vis" | "interactive-tool" | "control-surface" | "canvas-element";

export type SchemaNode =
  | { type: "string"; samples: string[] }
  | { type: "number"; min: number; max: number; integer: boolean }
  | { type: "boolean" }
  | { type: "null" }
  | { type: "array"; items: SchemaNode | null; length: number }
  | { type: "object"; properties: Record<string, SchemaNode> };

export interface ApiEndpoint {
  /** Raw literal found in the source, e.g. "https://api.acme.dev/v1/metrics" */
  raw: string;
  method: string;
  /** Normalized host, "" for relative URLs */
  host: string;
  /** Normalized path with dynamic segments replaced by ":param" */
  path: string;
  /** host + path, the matching key */
  signature: string;
}

export interface WidgetInput {
  name: string;
  type: string;
}

export interface Widget {
  id: string;
  name: string;
  description: string;
  tags: string[];
  type?: WidgetType;
  code: string;
  componentName: string;
  inputs: WidgetInput[];
  endpoints: ApiEndpoint[];
  /** Per-endpoint mock schema, keyed by endpoint signature */
  mocks: Record<string, { schema: SchemaNode | null; sample: unknown }>;
  createdAt: number;
}

export const endpointKey = (e: ApiEndpoint) => `${e.method} ${e.signature}`;
