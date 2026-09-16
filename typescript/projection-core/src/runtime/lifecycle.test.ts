import { applyViewPatch, RuntimeDisposer, validateViewPatch, type ViewPatch } from "./lifecycle.js";
import type { RuntimeView } from "./types.js";

function equal(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
}
function throws(action: () => void, message: string): void {
  try { action(); } catch { return; }
  throw new Error(message);
}

export function runLifecycleConformance(): void {
  const patch: ViewPatch = {
    schemaVersion: 1,
    viewId: "view-1",
    operations: [
      { op: "replace-widget-props", widgetId: "widget-1", props: { selected: true } },
      { op: "replace-widget-data", widgetId: "widget-1", data: [1, 2] },
      { op: "invalidate-adapter", adapterId: "adapter-1", reason: "stale source" },
    ],
  };
  equal(validateViewPatch(patch), true, "valid patch");
  equal(validateViewPatch({ ...patch, schemaVersion: 2 }), false, "version validation");
  equal(validateViewPatch({ ...patch, operations: [{ op: "replace-widget-props", widgetId: "widget-1", props: null }] }), false, "props validation");

  const widget = { props: { data: [], selected: false }, state: { selected: "preserve" } };
  const adapter = { status: "success" as const, lastError: undefined };
  const view = {
    spec: { id: "view-1" },
    widgets: new Map([["widget-1", widget]]),
    adapters: new Map([["adapter-1", adapter]]),
  } as unknown as RuntimeView;
  applyViewPatch(view, patch);
  equal(widget.props.selected, true, "props applied");
  equal(JSON.stringify(widget.props.data), JSON.stringify([1, 2]), "data applied");
  equal(widget.state.selected, "preserve", "state preserved");
  equal(adapter.status, "error", "adapter invalidated");
  throws(() => applyViewPatch(view, { ...patch, viewId: "other" }), "identity mismatch must fail");

  let cleanups = 0;
  const disposer = new RuntimeDisposer();
  const remove = disposer.add(() => { cleanups += 1; });
  disposer.add(() => { cleanups += 1; });
  equal(remove(), true, "cleanup removal reports removal");
  disposer.dispose();
  disposer.dispose();
  equal(cleanups, 1, "disposal is idempotent and removed cleanup is not called");
}
