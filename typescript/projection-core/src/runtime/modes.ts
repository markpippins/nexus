export type ViewRuntimeMode = "demo" | "fixture" | "live-read" | "live-governed";

export type ActionClass = "presentation" | "local-fixture" | "governed-domain";

export interface RuntimeModeState {
  mode: ViewRuntimeMode;
  dataSource: "synthetic" | "server";
  authoritative: false | "read" | "assessment" | "authority";
  label: string;
}

export interface PresentationResultState {
  kind: "assessment" | "authority";
  status: "pending" | "admitted" | "refused" | "routed" | "unknown" | "stale" | "drift" | "error";
  reason: string;
  envelopeId?: string;
  evaluationFingerprint?: string;
  admissionReceiptId?: string;
  transitionReceiptId?: string;
  evidenceIds?: string[];
  replayStatus?: string;
}

export function runtimeModeState(mode: ViewRuntimeMode): RuntimeModeState {
  switch (mode) {
    case "demo": return { mode, dataSource: "synthetic", authoritative: false, label: "DEMO" };
    case "fixture": return { mode, dataSource: "synthetic", authoritative: false, label: "FIXTURE" };
    case "live-read": return { mode, dataSource: "server", authoritative: "read", label: "LIVE READ" };
    case "live-governed": return { mode, dataSource: "server", authoritative: "authority", label: "LIVE GOVERNED" };
  }
}

export function classifyAction(actionType: string, mode: ViewRuntimeMode): ActionClass {
  if (["inspect", "drilldown", "filter", "sort", "navigate", "acknowledge", "dismiss", "compare"].includes(actionType)) {
    return "presentation";
  }
  if (mode === "demo" || mode === "fixture") return "local-fixture";
  return "governed-domain";
}

export function assertActionAllowed(actionClass: ActionClass, mode: ViewRuntimeMode): void {
  if (actionClass === "local-fixture" && mode !== "demo" && mode !== "fixture") {
    throw new Error("Fixture actions are only available in demo or fixture mode");
  }
  if (actionClass === "governed-domain" && mode !== "live-governed") {
    throw new Error("Governed domain actions require LIVE GOVERNED mode");
  }
}
