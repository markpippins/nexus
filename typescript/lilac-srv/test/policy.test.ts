/**
 * Static guards. These are the assertions that survive refactors: they read
 * the source rather than calling it, so a new handler cannot quietly acquire
 * a legacy write, an admission endpoint, or a non-inert default.
 */
import { readFileSync, readdirSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";
import { CANONICAL_TABLES } from "../src/contract";
import { ROUTE_TABLE } from "../src/routes";

const SRC = join(__dirname, "..", "src");
const sources = readdirSync(SRC)
  .filter((f) => f.endsWith(".ts"))
  .map((f) => ({ file: f, text: readFileSync(join(SRC, f), "utf-8") }));

describe("AC4 — canonical resolution.* only", () => {
  it("every table reference is schema-qualified resolution.* and allow-listed", () => {
    const offenders: string[] = [];
    const refs = new Set<string>();
    const pattern = /\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)/g;
    for (const { file, text } of sources) {
      for (const match of text.matchAll(pattern)) {
        const name = match[1];
        if (name.startsWith("$")) continue;
        refs.add(name);
        const [schema, table] = name.split(".");
        if (schema !== "resolution") {
          offenders.push(`${file}: ${name} (not in resolution schema)`);
        } else if (!(CANONICAL_TABLES as readonly string[]).includes(table)) {
          offenders.push(`${file}: ${name} (table not in CANONICAL_TABLES)`);
        }
      }
    }
    expect(offenders).toEqual([]);
    // The guard is only meaningful if it actually found statements.
    expect(refs.size).toBeGreaterThan(5);
    expect([...refs].every((r) => r.startsWith("resolution."))).toBe(true);
  });

  it("names no legacy schema anywhere in the source", () => {
    const legacy = /\b(vision|execution|wind|kernel|conduit|peb)\.(receipt|ticket|ticket_transition|fanout_transition|workflow|admission)\b/;
    const offenders = sources.filter(({ text }) => legacy.test(text)).map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("never writes the legacy ticket/receipt tables in another schema", () => {
    const offenders = sources.filter(({ text }) =>
      /\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?!resolution\.)[A-Za-z_][A-Za-z0-9_]*\./.test(text),
    );
    expect(offenders.map((s) => s.file)).toEqual([]);
  });
});

describe("AC7 — no admission or workflow-definition authority", () => {
  it("exposes no admission or workflow-definition route", () => {
    for (const route of ROUTE_TABLE) {
      expect(route.openapiPath.toLowerCase()).not.toContain("admission");
      expect(route.openapiPath.toLowerCase()).not.toContain("workflow-definition");
      expect(route.operationId.toLowerCase()).not.toContain("admission");
      expect(route.operationId.toLowerCase()).not.toContain("workflowdefinition");
    }
  });

  it("has no admission table in the allow-list", () => {
    expect(CANONICAL_TABLES as readonly string[]).not.toContain("admission");
    expect(CANONICAL_TABLES as readonly string[]).not.toContain("workflow_definition");
  });

  it("declares the two authorities it does NOT own", async () => {
    const { createApp } = await import("../src/app");
    const { FakeDb } = await import("./fake-db");
    const request = (await import("supertest")).default;
    const res = await request(createApp({ db: new FakeDb(), limiter: false })).get("/v1/meta");
    expect(res.body.authorities_not_owned).toEqual([
      "peb_admission",
      "wind_workflow_definition",
    ]);
  });
});

describe("AC6 — the service is staged-inert by construction", () => {
  it("reports staged_inert with no live callers", async () => {
    const { createApp } = await import("../src/app");
    const { FakeDb } = await import("./fake-db");
    const request = (await import("supertest")).default;
    const res = await request(createApp({ db: new FakeDb(), limiter: false })).get("/v1/meta");
    expect(res.body.staged_inert).toBe(true);
    expect(res.body.live_callers).toEqual([]);
  });

  it("contains no caller-repointing switch (no shadow/redirect env read)", () => {
    const offenders = sources.filter(({ text }) =>
      /CONDUIT_LILAC_SHADOW|CONDUIT_LILAC_URL|CONDUIT_LILAC_REDIRECT/.test(text),
    );
    // The service never decides who points at it; that decision lives with the
    // caller (conduit), whose default path a Python guard pins (AC6).
    expect(offenders.map((s) => s.file)).toEqual([]);
  });
});
