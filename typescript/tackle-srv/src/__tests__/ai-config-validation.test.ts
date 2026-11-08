import { describe, expect, it } from "vitest";
import {
  bundleNeedsBinary,
  configValidationErrors,
  validateAIConfigSnapshot,
} from "../ai-config-validation.js";

const harness = (id: string, binary: string | null, name = id) => ({
  id,
  name,
  invocation_semantics: JSON.stringify({ binary }),
});

const model = (
  id: string,
  harnessId = "harn-opencode",
  providerId: string | null = "prov-a",
) => ({
  id,
  name: id,
  harness_id: harnessId,
  provider_id: providerId,
  model_identifier: id,
});

const bundle = (over: any = {}) => ({
  id: "bundle-a",
  name: "Bundle A",
  role: "planner",
  model_id: "mod-a",
  priority: 1,
  is_active: 1,
  invocation_mode: "CLI",
  ...over,
});

const snapshot = (over: any = {}) => ({
  providers: [{ id: "prov-a", name: "Provider A", type: "opencode" }],
  harnesses: [
    harness("harn-opencode", "opencode"),
    harness("harn-freebuff", null, "Freebuff (hosted)"),
  ],
  models: [model("mod-a")],
  roles: [
    {
      role: "planner",
      model_id: "mod-a",
      provider_id: "prov-a",
      harness_id: "harn-opencode",
    },
  ],
  bundles: [bundle()],
  ...over,
});

const findings = (cfg: any) => validateAIConfigSnapshot(cfg as any);

describe("validateAIConfigSnapshot — severity contract", () => {
  it("returns no findings for a healthy config", () => {
    expect(findings(snapshot())).toEqual([]);
  });

  it("keeps a broken ACTIVE bundle fatal", () => {
    const cfg = snapshot({ bundles: [bundle({ model_id: "mod-missing" })] });
    const errors = configValidationErrors(findings(cfg));
    expect(errors).toHaveLength(1);
    expect(errors[0].field).toBe("model_id");
    expect(errors[0].severity).toBe("error");
  });

  it("does not let an INACTIVE bundle with a missing model block anything", () => {
    const cfg = snapshot({
      bundles: [
        bundle(),
        bundle({
          id: "bundle-retired",
          name: "historical gemini bundle",
          model_id: "mod-gemini-3-5-flash-genai",
          priority: 8,
          is_active: 0,
        }),
      ],
    });
    const all = findings(cfg);
    expect(configValidationErrors(all)).toEqual([]);
    const residue = all.find((w) => w.field === "model_id" && w.role === "planner");
    expect(residue?.severity).toBe("info");
    expect(residue?.message).toMatch(/inactive historical bundle/);
  });

  it("keeps a broken ACTIVE primary fatal", () => {
    const cfg = snapshot({ models: [] });
    const errors = configValidationErrors(findings(cfg));
    expect(errors.length).toBeGreaterThan(0);
    expect(errors.every((e) => e.severity === "error")).toBe(true);
  });

  it("does not demand a CLI binary from an INTERACTIVE primary", () => {
    const cfg = snapshot({
      models: [model("mod-hosted", "harn-freebuff")],
      roles: [
        {
          role: "leased-builder",
          model_id: "mod-hosted",
          provider_id: "prov-a",
          harness_id: "harn-freebuff",
        },
      ],
      bundles: [
        bundle({
          id: "cb-leased-builder-interactive",
          role: "leased-builder",
          model_id: "mod-hosted",
          priority: 0,
          invocation_mode: "INTERACTIVE",
        }),
      ],
    });
    expect(findings(cfg)).toEqual([]);
  });

  it("still fails a CLI bundle whose harness has no binary", () => {
    const cfg = snapshot({
      models: [model("mod-hosted", "harn-freebuff")],
      roles: [
        {
          role: "planner",
          model_id: "mod-hosted",
          provider_id: "prov-a",
          harness_id: "harn-freebuff",
        },
      ],
      bundles: [
        bundle({ model_id: "mod-hosted", invocation_mode: "CLI" }),
      ],
    });
    const errors = configValidationErrors(findings(cfg));
    expect(errors.some((e) => e.field === "invocation_semantics.binary")).toBe(true);
  });

  it("treats a missing provider as advisory, not fatal", () => {
    const cfg = snapshot({
      models: [model("mod-a", "harn-opencode", "prov-gone")],
    });
    const all = findings(cfg);
    expect(configValidationErrors(all)).toEqual([]);
    expect(all.some((w) => w.field === "provider_id" && w.severity === "warning")).toBe(
      true,
    );
  });
});

describe("bundleNeedsBinary", () => {
  it("only CLI needs a local binary", () => {
    expect(bundleNeedsBinary("CLI")).toBe(true);
    expect(bundleNeedsBinary(undefined)).toBe(true);
    expect(bundleNeedsBinary("INTERACTIVE")).toBe(false);
    expect(bundleNeedsBinary("HTTP")).toBe(false);
    expect(bundleNeedsBinary("SDK")).toBe(false);
    expect(bundleNeedsBinary("MCP")).toBe(false);
  });
});
