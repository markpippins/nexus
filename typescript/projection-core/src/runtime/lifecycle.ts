import { RuntimeView } from "./types";

export interface ViewPatch {
  schemaVersion: 1;
  viewId: string;
  baseArtifactDigest?: string;
  operations: ViewPatchOperation[];
}

export type ViewPatchOperation =
  | { op: "replace-widget-props"; widgetId: string; props: Record<string, unknown> }
  | { op: "replace-widget-data"; widgetId: string; data: unknown }
  | { op: "invalidate-adapter"; adapterId: string; reason: string };

export function validateViewPatch(value: unknown): value is ViewPatch {
  if (!value || typeof value !== "object") return false;
  const patch = value as Partial<ViewPatch>;
  return patch.schemaVersion === 1
    && typeof patch.viewId === "string"
    && Array.isArray(patch.operations)
    && patch.operations.every((operation) => {
      if (!operation || typeof operation !== "object" || typeof operation.op !== "string") return false;
      if (operation.op === "replace-widget-props") return typeof operation.widgetId === "string" && !!operation.props;
      if (operation.op === "replace-widget-data") return typeof operation.widgetId === "string";
      return operation.op === "invalidate-adapter" && typeof operation.adapterId === "string" && typeof operation.reason === "string";
    });
}

export function applyViewPatch(view: RuntimeView, patch: ViewPatch): void {
  if (!validateViewPatch(patch) || patch.viewId !== view.spec.id) {
    throw new Error("Invalid patch or patch view identity mismatch");
  }
  for (const operation of patch.operations) {
    if (operation.op === "replace-widget-props") {
      const widget = view.widgets.get(operation.widgetId);
      if (!widget) throw new Error(`Widget not found: ${operation.widgetId}`);
      widget.props = { ...widget.props, ...operation.props };
    } else if (operation.op === "replace-widget-data") {
      const widget = view.widgets.get(operation.widgetId);
      if (!widget) throw new Error(`Widget not found: ${operation.widgetId}`);
      widget.props.data = operation.data;
    } else {
      const adapter = view.adapters.get(operation.adapterId);
      if (!adapter) throw new Error(`Adapter not found: ${operation.adapterId}`);
      adapter.status = "error";
      adapter.lastError = new Error(operation.reason);
    }
  }
}

export class RuntimeDisposer {
  private cleanups = new Set<() => void>();

  add(cleanup: () => void): () => void {
    this.cleanups.add(cleanup);
    return () => this.cleanups.delete(cleanup);
  }

  addEventListener(target: EventTarget, type: string, handler: EventListener): void {
    target.addEventListener(type, handler);
    this.add(() => target.removeEventListener(type, handler));
  }

  dispose(): void {
    for (const cleanup of this.cleanups) cleanup();
    this.cleanups.clear();
  }
}
