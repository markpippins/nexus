/**
 * Tests for withoutPrimaryBundle — the filter that stops `GET
 * /config/ai/resolve/:role` from listing the primary model again as its own
 * first fallback.
 *
 * Pure functions only: no database, no pool, no service bootstrap.
 */
import { describe, it, expect } from "vitest";
import { withoutPrimaryBundle } from "../resolved-fallbacks.js";

type Rung = { bundle_id?: string | null; priority: number; model_identifier: string };

const rung = (
  bundle_id: string | null | undefined,
  priority: number,
  model_identifier: string,
): Rung => ({ bundle_id, priority, model_identifier });

describe("withoutPrimaryBundle", () => {
  it("drops the bundle the resolver already chose as the primary", () => {
    const ladder = [
      rung("bundle-primary", 1, "nvidia/nemotron-3.5-super-vl-120b-a12b"),
      rung("bundle-nvultra", 2, "nvidia/nemotron-3-ultra-550b-a55b"),
      rung("bundle-bigpickle", 3, "big-pickle"),
    ];

    expect(withoutPrimaryBundle(ladder, "bundle-primary")).toEqual([
      rung("bundle-nvultra", 2, "nvidia/nemotron-3-ultra-550b-a55b"),
      rung("bundle-bigpickle", 3, "big-pickle"),
    ]);
  });

  it("keeps the same model under a different bundle (another provider is another rung)", () => {
    const ladder = [
      rung("bundle-primary", 1, "glm-5.3-flash"),
      rung("bundle-nvidia-copy", 2, "glm-5.3-flash"),
      rung("bundle-go-copy", 3, "glm-5.3-flash"),
    ];

    expect(withoutPrimaryBundle(ladder, "bundle-primary").map((r) => r.bundle_id)).toEqual([
      "bundle-nvidia-copy",
      "bundle-go-copy",
    ]);
  });

  it("leaves the ladder untouched when the primary bundle is not in it", () => {
    const ladder = [rung("bundle-a", 1, "big-pickle"), rung("bundle-b", 2, "glm-5.3-flash")];

    expect(withoutPrimaryBundle(ladder, "bundle-not-present")).toEqual(ladder);
  });

  it("leaves the ladder untouched when the primary bundle id is missing", () => {
    const ladder = [rung("bundle-a", 1, "big-pickle")];

    expect(withoutPrimaryBundle(ladder, null)).toEqual(ladder);
    expect(withoutPrimaryBundle(ladder, undefined)).toEqual(ladder);
    expect(withoutPrimaryBundle(ladder, "")).toEqual(ladder);
  });

  it("keeps rows that carry no bundle id at all", () => {
    const ladder = [rung(undefined, 1, "big-pickle"), rung(null, 2, "glm-5.3-flash")];

    expect(withoutPrimaryBundle(ladder, "bundle-primary")).toEqual(ladder);
  });

  it("returns a copy instead of mutating the caller's list", () => {
    const ladder = [rung("bundle-primary", 1, "a"), rung("bundle-b", 2, "b")];
    const snapshot = [...ladder];

    const filtered = withoutPrimaryBundle(ladder, "bundle-primary");
    filtered.pop();

    expect(ladder).toEqual(snapshot);
    expect(withoutPrimaryBundle(ladder, null)).not.toBe(ladder);
  });
});
