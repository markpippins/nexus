import {
  CapabilityId,
  RoleSpec,
  InteractionSpec,
  DensitySetting,
  SurfaceContextSpec,
} from "../types/designIR";
import { AdapterStub } from "../types/viewSpec";

/**
 * Canonical Adapter Suggestion Heuristics
 *
 * Rules:
 * 1. Contract-driven: Look at capability contract, not payload schema.
 * 2. Role-driven: Primary roles get map + sortBy; secondary get select; ambient get select + filter.
 * 3. Interaction-driven: inspect -> semanticMap, filter -> filter, compare -> groupBy.
 * 4. Context & Density: nearRealTime/realTime -> distinct + sortBy; highSalience -> map + semanticMap.
 * 5. Hard invariant: Compiler emits <unknown> placeholder tokens. Studio/runtime resolves them.
 */
export function suggestAdapterStub(
  capabilityId: CapabilityId,
  role: RoleSpec,
  interactions: InteractionSpec[] = [],
  effectiveDensity: DensitySetting = "normal",
  context?: SurfaceContextSpec,
): AdapterStub {
  const steps: Array<{ op: string; args?: Record<string, unknown> }> = [];

  // Base select step
  steps.push({
    op: "select",
    args: { path: "<unknown>" },
  });

  // Capability-Driven Rules
  if (capabilityId === "MetricSeries") {
    steps.push({
      op: "map",
      args: {
        fields: {
          x: "<unknown>",
          y: "<unknown>",
          unit: "<unknown>",
        },
      },
    });
  }

  // Role-Driven Rules
  if (role.priority === "primary" || effectiveDensity === "highSalience") {
    if (capabilityId !== "MetricSeries") {
      steps.push({
        op: "map",
        args: { fields: {} },
      });
    }
    steps.push({
      op: "sortBy",
      args: { key: "<unknown>", direction: "asc" },
    });
  }

  if (role.priority === "ambient") {
    steps.push({
      op: "filter",
      args: { predicate: "<unknown>" },
    });
  }

  // Interaction-Driven Rules
  const hasInspect = interactions.some(
    (i) => i.verb === "inspect" && (i.sourceRole === role.label || i.targetRole === role.label),
  );
  const hasFilter = interactions.some((i) => i.verb === "filter");
  const hasCompare = interactions.some((i) => i.verb === "compare");

  if (hasInspect) {
    steps.push({
      op: "semanticMap",
      args: { map: {} },
    });
  }

  if (hasFilter && !steps.some((s) => s.op === "filter")) {
    steps.push({
      op: "filter",
      args: { predicate: "<unknown>" },
    });
  }

  if (hasCompare) {
    steps.push({
      op: "groupBy",
      args: { key: "<unknown>" },
    });
  }

  // Context-Driven Rules
  if (context?.timeSensitivity === "realTime" || context?.timeSensitivity === "nearRealTime") {
    steps.push({
      op: "distinct",
      args: { key: "<unknown>" },
    });
  }

  if (context?.reliabilityBias === "strong" || context?.reliabilityBias === "strict") {
    steps.push({
      op: "coalesce",
      args: { fields: [] },
    });
  }

  return { steps };
}
