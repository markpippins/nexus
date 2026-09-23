/**
 * Hermetic parity tests for the voyager port's response shaping.
 *
 * SCOPE — these pin the pure semantics that must match typescript/voyager-srv:
 *   - snake_case → camelCase key mapping (camelCaseRow)
 *   - pg `timestamptz`/`timestamp` Date → epoch millis
 *   - numeric query-string coercion with fallbacks (toNumber)
 *   - the per-endpoint page window arithmetic (pageWindow)
 *
 * NOT COVERED HERE — the route surface. That is enforced by the apidocs drift
 * gate (`make apidocs-validate`), which reads this app's gateway alias map
 * against typescript/voyager-srv/openapi.yaml (check_drift.MOLECULER_MIRRORS)
 * and fails CI on any added/removed/renamed path. Duplicating that assertion
 * in jest would only create a second place to drift.
 *
 * No database, no broker, no HTTP bind: importing the service module must not
 * connect anything (the pool is created in the service's `created()` hook).
 */
import {
  camelCaseRow,
  camelCaseRows,
  pageWindow,
  toNumber,
} from "../services/voyager.service";

describe("toNumber (query-string coercion)", () => {
  it("passes numbers through and parses numeric strings", () => {
    expect(toNumber(3, 1)).toBe(3);
    expect(toNumber("3", 1)).toBe(3);
    expect(toNumber("0", 1)).toBe(0);
  });

  it("falls back on absent/NaN/non-numeric values", () => {
    expect(toNumber(undefined, 20)).toBe(20);
    expect(toNumber(null, 20)).toBe(0); // Number(null) === 0, as in the incumbent
    expect(toNumber("abc", 20)).toBe(20);
    expect(toNumber(NaN, 20)).toBe(20);
    expect(toNumber({}, 20)).toBe(20);
  });
});

describe("pageWindow (per-endpoint pagination)", () => {
  it("uses the endpoint default when pageSize is absent", () => {
    expect(pageWindow({}, 20)).toEqual({ page: 1, pageSize: 20, offset: 0 });
    expect(pageWindow({}, 50)).toEqual({ page: 1, pageSize: 50, offset: 0 });
  });

  it("clamps pageSize to [1, 100] and page to >= 1", () => {
    expect(pageWindow({ pageSize: "500" }, 50).pageSize).toBe(100);
    expect(pageWindow({ pageSize: "0" }, 50).pageSize).toBe(1);
    expect(pageWindow({ pageSize: "-5" }, 50).pageSize).toBe(1);
    expect(pageWindow({ page: "0" }, 50).page).toBe(1);
    expect(pageWindow({ page: "-3" }, 50).page).toBe(1);
  });

  it("computes offset from the clamped window", () => {
    expect(pageWindow({ page: "3", pageSize: "25" }, 50).offset).toBe(50);
    expect(pageWindow({ page: "2" }, 20).offset).toBe(20);
    // pageSize clamped to 100 first, then offset derives from it
    expect(pageWindow({ page: "2", pageSize: "1000" }, 50).offset).toBe(100);
  });
});

describe("camelCaseRow (column → JSON envelope)", () => {
  it("maps snake_case keys to camelCase", () => {
    expect(
      camelCaseRow({
        scan_epoch_id: "e1",
        device_id: 42,
        stability_score: 0.5,
        observation_id: "o1",
        id: "x",
      })
    ).toEqual({
      scanEpochId: "e1",
      deviceId: 42,
      stabilityScore: 0.5,
      observationId: "o1",
      id: "x",
    });
  });

  it("converts JS Dates (pg timestamp columns) to epoch millis", () => {
    const d = new Date("2026-09-22T10:30:00.000Z");
    const row = camelCaseRow({ discovered_at: d, started_at: d });
    expect(row.discoveredAt).toBe(d.getTime());
    expect(row.startedAt).toBe(d.getTime());
    expect(typeof row.discoveredAt).toBe("number");
  });

  it("leaves jsonb objects, arrays, nulls and booleans intact", () => {
    const structure = { type: "directory", children: 3 };
    const row = camelCaseRow({
      structure,
      provenance: ["a", "b"],
      confidence: null,
      event_candidate: true,
    });
    expect(row.structure).toBe(structure);
    expect(row.provenance).toEqual(["a", "b"]);
    expect(row.confidence).toBeNull();
    expect(row.eventCandidate).toBe(true);
  });
});

describe("camelCaseRows", () => {
  it("preserves row order (the incumbent's ORDER BY is the contract)", () => {
    const rows = camelCaseRows([
      { span_type: "heading", start_pos: 0 },
      { span_type: "paragraph", start_pos: 12 },
      { span_type: "code", start_pos: 40 },
    ]);
    expect(rows.map((r) => r.spanType)).toEqual(["heading", "paragraph", "code"]);
    expect(rows.map((r) => r.startPos)).toEqual([0, 12, 40]);
  });

  it("handles an empty result set", () => {
    expect(camelCaseRows([])).toEqual([]);
  });
});
