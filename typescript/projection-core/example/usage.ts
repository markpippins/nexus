import { DesignIR } from "../src/types/designIR";
import { DesignIRCompiler } from "../src/compiler/compiler";

export const exampleDesignIR: DesignIR = {
  name: "Execution Overview",
  roles: {
    primaryMetric: {
      label: "Execution Health",
      capability: {
        id: "MetricSeries",
        variant: "sparkline",
        schemaHint: { fields: ["timestamp", "value"] },
      },
      priority: "primary",
      density: "highSalience",
      interactions: ["inspect", "drilldown"],
    },
    workQueue: {
      label: "Active Requests",
      capability: { id: "EntityCollection" },
      priority: "secondary",
      density: "normal",
      interactions: ["inspect", "navigate", "filter"],
      constraints: { allowMultiSelect: true },
    },
    auditStream: {
      label: "Recent Events",
      capability: { id: "Timeline" },
      priority: "ambient",
      density: "compact",
      interactions: ["inspect"],
    },
  },
  interactions: [
    { verb: "inspect", sourceRole: "workQueue", targetRole: "primaryMetric" },
    { verb: "inspect", sourceRole: "auditStream", targetRole: "primaryMetric" },
    { verb: "navigate", sourceRole: "workQueue" },
  ],
  density: "normal",
  hierarchy: {
    primaryRoles: ["primaryMetric"],
    secondaryRoles: ["workQueue"],
    ambientRoles: ["auditStream"],
  },
  context: {
    surfaceType: "dashboard",
    timeSensitivity: "nearRealTime",
    reliabilityBias: "strong",
  },
};

const compiler = new DesignIRCompiler();
const viewSpec = compiler.compileDesignIR(exampleDesignIR);

console.log("=== Compiled ViewSpec ===");
console.log(JSON.stringify(viewSpec, null, 2));
