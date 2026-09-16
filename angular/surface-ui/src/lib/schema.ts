import type { SchemaNode } from "./widget-types";

/** Infer a mock-friendly schema from a sample JSON payload. */
export function inferSchema(value: unknown): SchemaNode {
  if (value === null || value === undefined) return { type: "null" };
  if (Array.isArray(value)) {
    const items = value.length ? mergeMany(value.map(inferSchema)) : null;
    return { type: "array", items, length: Math.max(value.length, 1) };
  }
  if (typeof value === "object") {
    const properties: Record<string, SchemaNode> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      properties[k] = inferSchema(v);
    }
    return { type: "object", properties };
  }
  if (typeof value === "number") {
    const integer = Number.isInteger(value);
    const spread = Math.max(Math.abs(value) * 0.25, integer ? 2 : 0.5);
    return { type: "number", min: value - spread, max: value + spread, integer };
  }
  if (typeof value === "boolean") return { type: "boolean" };
  return { type: "string", samples: [String(value)] };
}

function mergeMany(nodes: SchemaNode[]): SchemaNode {
  return nodes.reduce((a, b) => merge(a, b));
}

function merge(a: SchemaNode, b: SchemaNode): SchemaNode {
  if (a.type !== b.type) return a.type === "null" ? b : a;
  if (a.type === "number" && b.type === "number") {
    return {
      type: "number",
      min: Math.min(a.min, b.min),
      max: Math.max(a.max, b.max),
      integer: a.integer && b.integer,
    };
  }
  if (a.type === "string" && b.type === "string") {
    return {
      type: "string",
      samples: Array.from(new Set([...a.samples, ...b.samples])).slice(0, 24),
    };
  }
  if (a.type === "array" && b.type === "array") {
    return {
      type: "array",
      items: a.items && b.items ? merge(a.items, b.items) : (a.items ?? b.items),
      length: Math.max(a.length, b.length),
    };
  }
  if (a.type === "object" && b.type === "object") {
    const properties: Record<string, SchemaNode> = { ...a.properties };
    for (const [k, v] of Object.entries(b.properties)) {
      properties[k] = properties[k] ? merge(properties[k], v) : v;
    }
    return { type: "object", properties };
  }
  return a;
}

const WORDS = [
  "alpha",
  "bravo",
  "delta",
  "echo",
  "kilo",
  "nova",
  "orbit",
  "quartz",
  "relay",
  "sierra",
  "vector",
  "zenith",
];

/**
 * Deterministic pseudo-random generator so a given (seed, path) always yields
 * a smooth, animatable value rather than jittering chaos.
 */
function noise(seed: number, path: string): number {
  let h = 2166136261;
  for (let i = 0; i < path.length; i++) {
    h ^= path.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const phase = (h >>> 0) % 1000;
  return (Math.sin(seed / 6 + phase) + 1) / 2;
}

/** Produce a mock payload for a schema at animation frame `tick`. */
export function generateMock(node: SchemaNode | null, tick: number, path = "$"): unknown {
  if (!node) return null;
  switch (node.type) {
    case "null":
      return null;
    case "boolean":
      return noise(tick, path) > 0.5;
    case "number": {
      const raw = node.min + noise(tick, path) * (node.max - node.min);
      return node.integer ? Math.round(raw) : Math.round(raw * 100) / 100;
    }
    case "string": {
      const pool = node.samples.length ? node.samples : WORDS;
      if (pool.length === 1) {
        const only = pool[0]!;
        // Keep stable strings stable (ids, labels) but animate ISO timestamps.
        if (/^\d{4}-\d{2}-\d{2}T/.test(only))
          return new Date(Date.now() + tick * 1000).toISOString();
        return only;
      }
      return pool[Math.floor(noise(tick, path) * pool.length) % pool.length]!;
    }
    case "array": {
      const len = Math.max(1, node.length);
      return Array.from({ length: len }, (_, i) =>
        generateMock(node.items, tick + i, `${path}[${i}]`),
      );
    }
    case "object": {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(node.properties)) {
        out[k] = generateMock(v, tick, `${path}.${k}`);
      }
      return out;
    }
  }
}

/** Flatten a schema into "path: type" lines for display and for matching. */
export function schemaFields(node: SchemaNode | null, path = ""): string[] {
  if (!node) return [];
  if (node.type === "object") {
    return Object.entries(node.properties).flatMap(([k, v]) =>
      schemaFields(v, path ? `${path}.${k}` : k),
    );
  }
  if (node.type === "array") {
    return schemaFields(node.items, `${path}[]`);
  }
  return [`${path || "$"}: ${node.type}`];
}
