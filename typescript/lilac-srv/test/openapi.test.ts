/**
 * AC1: a route in openapi.yaml with no handler fails, and a handler with no
 * documented route fails. Both directions are asserted against the SAME table
 * the router is built from, so there is no third place to drift.
 */
import { readFileSync } from "fs";
import { join } from "path";
import { parse } from "yaml";
import { describe, expect, it } from "vitest";
import { ROUTE_TABLE } from "../src/routes";

const spec = parse(
  readFileSync(join(__dirname, "..", "openapi.yaml"), "utf-8"),
) as {
  paths: Record<string, Record<string, { operationId?: string; responses?: Record<string, unknown> }>>;
};

const specRoutes = new Set<string>();
for (const [path, operations] of Object.entries(spec.paths)) {
  for (const method of Object.keys(operations)) {
    specRoutes.add(`${method.toUpperCase()} ${path}`);
  }
}
const codeRoutes = new Set(ROUTE_TABLE.map((r) => r.envelopeRoute));

describe("OpenAPI ↔ handler parity", () => {
  it("documents every route the server registers", () => {
    const undocumented = [...codeRoutes].filter((r) => !specRoutes.has(r));
    expect(undocumented).toEqual([]);
  });

  it("has a handler for every documented route", () => {
    const unimplemented = [...specRoutes].filter((r) => !codeRoutes.has(r));
    expect(unimplemented).toEqual([]);
  });

  it("uses the same operationId on both sides", () => {
    for (const route of ROUTE_TABLE) {
      const [, path] = route.envelopeRoute.split(" ");
      const op = spec.paths[path]?.[route.method];
      expect(op?.operationId).toBe(route.operationId);
    }
  });

  it("documents the refusal statuses every mutation can return", () => {
    for (const route of ROUTE_TABLE.filter((r) => r.mutation)) {
      const [, path] = route.envelopeRoute.split(" ");
      const responses = Object.keys(spec.paths[path][route.method].responses ?? {});
      expect(responses).toEqual(expect.arrayContaining(["409", "422"]));
    }
  });

  it("declares no admission or workflow-definition path (AC7)", () => {
    for (const path of Object.keys(spec.paths)) {
      expect(path.toLowerCase()).not.toContain("admission");
      expect(path.toLowerCase()).not.toContain("workflow-definition");
    }
  });
});
