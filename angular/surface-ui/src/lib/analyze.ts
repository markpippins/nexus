import type { ApiEndpoint, WidgetInput } from "./widget-types";

const METHOD_RE = /method\s*:\s*["'`](GET|POST|PUT|PATCH|DELETE|HEAD)["'`]/i;

function normalizeUrl(raw: string): { host: string; path: string } {
  let host = "";
  let path = raw;
  const abs = /^https?:\/\/([^/?#]+)([^?#]*)/i.exec(raw);
  if (abs) {
    host = abs[1]!.toLowerCase();
    path = abs[2] || "/";
  } else {
    path = raw.split("?")[0] || "/";
  }
  path = path
    .replace(/\$\{[^}]*\}/g, ":param")
    .replace(/\/\d+(?=\/|$)/g, "/:param")
    .replace(/\/+$/, "");
  if (!path.startsWith("/")) path = "/" + path;
  return { host, path };
}

/** Find REST calls (fetch / axios / ky) in pasted component source. */
export function detectEndpoints(code: string): ApiEndpoint[] {
  const found = new Map<string, ApiEndpoint>();
  const push = (raw: string, method: string) => {
    if (!raw || raw.startsWith("data:")) return;
    const { host, path } = normalizeUrl(raw);
    const signature = `${host}${path}`;
    const key = `${method} ${signature}`;
    if (!found.has(key)) found.set(key, { raw, method, host, path, signature });
  };

  const patterns: Array<{ re: RegExp; method?: string }> = [
    { re: /fetch\s*\(\s*[`"']([^`"']+)[`"']\s*(?:,\s*\{([\s\S]{0,200}?)\})?/g },
    { re: /axios\.(get|post|put|patch|delete)\s*\(\s*[`"']([^`"']+)[`"']/g, method: "AXIOS" },
    { re: /ky\.(get|post|put|patch|delete)\s*\(\s*[`"']([^`"']+)[`"']/g, method: "AXIOS" },
  ];

  for (const { re, method } of patterns) {
    let m: RegExpExecArray | null;
    while ((m = re.exec(code)) !== null) {
      if (method === "AXIOS") {
        push(m[2]!, m[1]!.toUpperCase());
      } else {
        const opts = m[2] ?? "";
        const mm = METHOD_RE.exec(opts);
        push(m[1]!, (mm?.[1] ?? "GET").toUpperCase());
      }
    }
  }
  return [...found.values()];
}

/** Detect the exported component name. */
export function detectComponentName(code: string): string {
  const patterns = [
    /export\s+default\s+function\s+([A-Z]\w*)/,
    /export\s+default\s+([A-Z]\w*)\s*;?/,
    /export\s+function\s+([A-Z]\w*)/,
    /export\s+const\s+([A-Z]\w*)\s*[:=]/,
    /function\s+([A-Z]\w*)\s*\(/,
    /const\s+([A-Z]\w*)\s*=\s*\(/,
  ];
  for (const re of patterns) {
    const m = re.exec(code);
    if (m) return m[1]!;
  }
  return "Widget";
}

/** Detect the component's input scheme (destructured props + optional TS types). */
export function detectInputs(code: string, componentName: string): WidgetInput[] {
  const types = new Map<string, string>();
  const typeBlock =
    /(?:interface|type)\s+\w*Props\w*\s*=?\s*\{([\s\S]*?)\n\}/.exec(code)?.[1] ??
    /\(\s*\{[^}]*\}\s*:\s*\{([\s\S]*?)\}\s*\)/.exec(code)?.[1];
  if (typeBlock) {
    for (const line of typeBlock.split(/[;\n,]/)) {
      const m = /^\s*(\w+)\s*\??\s*:\s*([^;]+)$/.exec(line);
      if (m) types.set(m[1]!, m[2]!.trim());
    }
  }

  const escaped = componentName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const sigRe = new RegExp(
    `(?:function\\s+${escaped}\\s*|const\\s+${escaped}\\s*(?::[^=]+)?=\\s*)\\(\\s*\\{([^}]*)\\}`,
  );
  const sig = sigRe.exec(code)?.[1];
  const names: string[] = [];
  if (sig) {
    for (const part of sig.split(",")) {
      const m = /^\s*(\w+)/.exec(part);
      if (m && !names.includes(m[1]!)) names.push(m[1]!);
    }
  } else {
    for (const k of types.keys()) names.push(k);
  }

  return names.map((name) => ({ name, type: types.get(name) ?? "unknown" }));
}

export function analyze(code: string) {
  const componentName = detectComponentName(code);
  return {
    componentName,
    inputs: detectInputs(code, componentName),
    endpoints: detectEndpoints(code),
  };
}
