/**
 * Unit test: the resolver's fallback list must not repeat its primary.
 *
 * The defect this guards: GET /config/ai/resolve/sysadmin returned
 * nvidia/nemotron-3-super-120b-a12b as `model_identifier` AND as the only
 * entry in `fallback_models` — the same bundle row selected twice.
 */

import { withoutPrimaryBundle } from "../services/resolved-fallbacks";

interface Row {
  bundle_id?: string | null;
  model_identifier: string;
}

const row = (bundle_id: string | null | undefined, model_identifier = "m"): Row => ({
  bundle_id,
  model_identifier,
});

describe("withoutPrimaryBundle", () => {
  it("drops the bundle row the resolver already chose as the primary", () => {
    const primary = row("bundle-primary", "nvidia/nemotron-3-super-120b-a12b");
    const other = row("bundle-fallback", "big-pickle");
    expect(withoutPrimaryBundle([primary, other], "bundle-primary")).toEqual([other]);
  });

  it("keeps the same model when it is a different bundle under another provider", () => {
    const nvidiaCopy = row("bundle-nvidia", "z-ai/glm-5.2");
    const openrouterCopy = row("bundle-openrouter", "z-ai/glm-5.2");
    expect(withoutPrimaryBundle([nvidiaCopy, openrouterCopy], "bundle-nvidia")).toEqual([
      openrouterCopy,
    ]);
  });

  it("leaves the list untouched when the primary bundle id is unknown", () => {
    const rows = [row("bundle-a"), row("bundle-b")];
    expect(withoutPrimaryBundle(rows, undefined)).toEqual(rows);
    expect(withoutPrimaryBundle(rows, null)).toEqual(rows);
    expect(withoutPrimaryBundle(rows, "")).toEqual(rows);
  });

  it("keeps rows that carry no bundle id", () => {
    const anonymous = row(undefined);
    expect(withoutPrimaryBundle([anonymous], "bundle-primary")).toEqual([anonymous]);
  });

  it("does not mutate its input", () => {
    const rows = [row("bundle-primary"), row("bundle-fallback")];
    const snapshot = [...rows];
    withoutPrimaryBundle(rows, "bundle-primary");
    expect(rows).toEqual(snapshot);
  });

  it("returns a copy even when nothing is filtered", () => {
    const rows = [row("bundle-a")];
    const result = withoutPrimaryBundle(rows, "bundle-primary");
    expect(result).toEqual(rows);
    expect(result).not.toBe(rows);
  });
});
