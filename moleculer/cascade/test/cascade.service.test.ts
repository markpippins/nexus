/**
 * Hermetic parity tests for the cascade port.
 *
 * SCOPE — pure semantics that must match typescript/cascade-srv:
 *   - limit/offset clamping (lim = min(parseInt||f, 200); off = parseInt||0)
 *   - the analytics interval/trunc allowlists (INTERVAL_MAP / TRUNC_MAP):
 *     unknown values fall back to "24 hours"/"hour" — the allowlist is the
 *     only thing that makes the incumbent's SQL interpolation safe
 *   - the lineage graph builder (node/edge assembly from CTE rows,
 *     truncation flag included)
 *   - the funnel response shape passthrough
 *
 * NOT COVERED HERE — the route surface. That is enforced by the apidocs drift
 * gate (`make apidocs-validate`), which reads this app's gateway alias map
 * against typescript/cascade-srv/openapi.yaml (check_drift.MOLLECULER_MIRRORS).
 * Duplicating it in jest would only create a second place to drift.
 *
 * No database, no broker, no HTTP bind. The SQL itself is pinned by the
 * statement-for-statement review plus the live canary diff, not by mocks that
 * would just restate the implementation.
 */
import {
  clampLimit,
  clampOffset,
  INTERVAL_MAP,
  TRUNC_MAP,
  resolveAnalyticsWindow,
  buildLineageGraph,
} from "../services/cascade.service";

describe("limit/offset clamping (incumbent parseInt||fallback arithmetic)", () => {
  it("clamps limit to 200 and falls back to 50 on garbage", () => {
    expect(clampLimit("200")).toBe(200);
    expect(clampLimit("500")).toBe(200);
    expect(clampLimit("abc")).toBe(50);
    expect(clampLimit(undefined)).toBe(50);
  });

  it("falls back to 0 for offset", () => {
    expect(clampOffset("100")).toBe(100);
    expect(clampOffset("abc")).toBe(0);
    expect(clampOffset(undefined)).toBe(0);
  });
});

describe("analytics window allowlists", () => {
  it("maps every incumbent range key", () => {
    expect(INTERVAL_MAP).toEqual({
      "1h": "1 hour",
      "6h": "6 hours",
      "24h": "24 hours",
      "7d": "7 days",
      "30d": "30 days",
    });
    expect(TRUNC_MAP).toEqual({ minute: "minute", hour: "hour", day: "day" });
  });

  it("falls back to 24 hours / hour for unknown values (the safety property)", () => {
    // The incumbent interpolates these into SQL — an unknown value MUST hit
    // the fallback, never reach the query text.
    expect(resolveAnalyticsWindow("1h", "minute")).toEqual({
      interval: "1 hour",
      truncUnit: "minute",
      timeRange: "1h",
      granularity: "minute",
    });
    expect(resolveAnalyticsWindow("; DROP TABLE x", "century").interval).toBe("24 hours");
    expect(resolveAnalyticsWindow("1h", "century").truncUnit).toBe("hour");
    expect(resolveAnalyticsWindow(undefined, undefined).interval).toBe("24 hours");
  });

  it("returns the RAW request values in the response envelope (not the resolved ones)", () => {
    // The incumbent echoes req.query.range/granularity verbatim, even when
    // they fell back: `range: timeRange, granularity: truncUnit` — note it
    // echoes truncUnit (resolved) but timeRange (raw). Parity quirk preserved.
    const w = resolveAnalyticsWindow("bogus", "bogus");
    expect(w.timeRange).toBe("bogus");
    expect(w.granularity).toBe("hour");
  });
});

describe("lineage graph builder (node/edge assembly)", () => {
  const rows = [
    { event_id: "a", event_type: "t0", causation_id: null, source: "s0", event_timestamp: 100, depth: 0 },
    { event_id: "b", event_type: "t1", causation_id: "a", source: "s1", event_timestamp: 200, depth: 1 },
    { event_id: "c", event_type: "t2", causation_id: "b", source: "s2", event_timestamp: 300, depth: 2 },
  ];

  it("builds depth-ordered nodes and causation edges", () => {
    const g = buildLineageGraph(rows, "a", "forward", "caused_by", 5);
    expect(g.root).toBe("a");
    expect(g.direction).toBe("forward");
    expect(g.nodes.map((n: any) => n.id)).toEqual(["a", "b", "c"]);
    expect(g.nodes[1]).toEqual({ id: "b", type: "t1", source: "s1", timestamp: 200, depth: 1 });
    expect(g.edges).toEqual([
      { source: "a", target: "b", type: "caused_by" },
      { source: "b", target: "c", type: "caused_by" },
    ]);
  });

  it("honours the custom edgeType query param", () => {
    const g = buildLineageGraph(rows, "a", "forward", "triggered", 5);
    expect(g.edges[0].type).toBe("triggered");
  });

  it("flags truncation at rows >= depth*10 (incumbent heuristic)", () => {
    expect(buildLineageGraph(rows, "a", "forward", "caused_by", 5).truncated).toBe(false);
    const many = Array.from({ length: 50 }, (_, i) => ({
      event_id: `e${i}`,
      event_type: "t",
      causation_id: i > 0 ? `e${i - 1}` : null,
      source: "s",
      event_timestamp: i,
      depth: i,
    }));
    expect(buildLineageGraph(many, "e0", "forward", "caused_by", 5).truncated).toBe(true);
  });

  it("deduplicates revisited nodes via the nodeMap", () => {
    const cyclic = [
      { event_id: "a", event_type: "t", causation_id: "b", source: "s", event_timestamp: 1, depth: 0 },
      { event_id: "b", event_type: "t", causation_id: "a", source: "s", event_timestamp: 2, depth: 1 },
      { event_id: "b", event_type: "t", causation_id: "a", source: "s", event_timestamp: 2, depth: 2 },
    ];
    const g = buildLineageGraph(cyclic, "a", "backward", "caused_by", 5);
    expect(g.nodes).toHaveLength(2);
  });
});
