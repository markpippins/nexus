import type { Widget } from "./widget-types";

/** Reasonable stand-in values so a widget can render inside the catalog. */
export function defaultProps(widget: Widget): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const input of widget.inputs) {
    const t = input.type.toLowerCase();
    const n = input.name.toLowerCase();
    if (t.includes("number") || /ms$|count|limit|size|interval/.test(n)) {
      out[input.name] = /ms$|interval/.test(n) ? 1200 : 5;
    } else if (t.includes("boolean") || /^(is|has|show|enable)/.test(n)) {
      out[input.name] = true;
    } else if (t.includes("[]") || t.includes("array")) {
      out[input.name] = [];
    } else if (t.includes("=>") || t.includes("function")) {
      out[input.name] = () => {};
    } else {
      out[input.name] = "demo";
    }
  }
  return out;
}
