import * as React from "react";
import { transform } from "@babel/standalone";
import { generateMock } from "@/lib/schema";
import type { Widget } from "@/lib/widget-types";
import {
  assertComponentName,
  assertSandboxableCode,
  capMockPayload,
  clampIntervalMs,
} from "@/lib/sandbox-guard";

function normalize(url: string): string {
  let host = "";
  let path = url;
  const abs = /^https?:\/\/([^/?#]+)([^?#]*)/i.exec(url);
  if (abs) {
    host = abs[1]!.toLowerCase();
    path = abs[2] || "/";
  } else {
    path = url.split("?")[0] || "/";
  }
  path = path.replace(/\/\d+(?=\/|$)/g, "/:param").replace(/\/+$/, "");
  if (!path.startsWith("/")) path = "/" + path;
  return `${host}${path}`;
}

/** Build a fetch replacement that answers from the widget's mock schemas. */
function makeMockFetch(widget: Widget, tickRef: { current: number }, log: (line: string) => void) {
  const keys = Object.keys(widget.mocks);
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const sig = normalize(url);
    let key = keys.find((k) => k === sig);
    if (!key) key = keys.find((k) => sig.endsWith(k) || k.endsWith(sig));
    if (!key) key = keys[0];
    const entry = key ? widget.mocks[key] : undefined;
    const body = capMockPayload(
      entry?.schema ? generateMock(entry.schema, tickRef.current) : (entry?.sample ?? {}),
      log,
    );
    log(`${(init?.method ?? "GET").toUpperCase()} ${sig} → 200 mock`);
    await new Promise((r) => setTimeout(r, 60));
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => body,
      text: async () => JSON.stringify(body),
      clone() {
        return this;
      },
    } as unknown as Response;
  }) as unknown as typeof fetch;
}

/** Compile pasted JSX/TSX and bind a sandboxed fetch/axios into its scope. */
function compileWidget(
  code: string,
  componentName: string,
  mockFetch: typeof fetch,
): React.ComponentType<Record<string, unknown>> {
  const codeGuard = assertSandboxableCode(code);
  if (!codeGuard.ok) throw new Error(codeGuard.reason);
  const nameGuard = assertComponentName(componentName);
  if (!nameGuard.ok) throw new Error(nameGuard.reason);

  const stripped = code
    .replace(/^\s*import\s[\s\S]*?from\s*["'][^"']+["'];?\s*$/gm, "")
    .replace(/^\s*import\s+["'][^"']+["'];?\s*$/gm, "")
    .replace(/export\s+default\s+function/g, "function")
    .replace(/export\s+default\s+/g, "const __default = ")
    .replace(/export\s+(const|function|class)/g, "$1");

  const out = transform(stripped, {
    filename: "widget.tsx",
    presets: [["react", { runtime: "classic" }], "typescript"],
  }).code;

  const factory = new Function(
    "React",
    "useState",
    "useEffect",
    "useMemo",
    "useRef",
    "useCallback",
    "fetch",
    "axios",
    `"use strict";${out}\n;return typeof ${componentName} !== "undefined" ? ${componentName} : (typeof __default !== "undefined" ? __default : null);`,
  );

  const call = async (url: string) => ({ data: await (await mockFetch(url)).json() });
  const axios = Object.freeze({
    get: call,
    post: call,
    put: call,
    patch: call,
    delete: call,
  });

  const mod = factory(
    React,
    React.useState,
    React.useEffect,
    React.useMemo,
    React.useRef,
    React.useCallback,
    mockFetch,
    axios,
  );
  if (typeof mod !== "function")
    throw new Error(`No component named "${componentName}" was found.`);
  return mod as React.ComponentType<Record<string, unknown>>;
}

class Boundary extends React.Component<
  { children: React.ReactNode; onError: (e: Error) => void },
  { failed: boolean }
> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override componentDidCatch(error: Error) {
    this.props.onError(error);
  }
  override render() {
    if (this.state.failed) return null;
    return this.props.children;
  }
}

interface Props {
  widget: Widget;
  props?: Record<string, unknown>;
  /** milliseconds between mock data frames */
  intervalMs?: number;
  onLog?: (line: string) => void;
  className?: string;
}

export function WidgetSandbox({ widget, props = {}, intervalMs = 900, onLog, className }: Props) {
  const tickRef = React.useRef(0);
  const [error, setError] = React.useState<string | null>(null);
  const logRef = React.useRef(onLog);
  logRef.current = onLog;

  const Component = React.useMemo(() => {
    try {
      const mockFetch = makeMockFetch(widget, tickRef, (l) => logRef.current?.(l));
      return compileWidget(widget.code, widget.componentName, mockFetch);
    } catch (e) {
      return e instanceof Error ? e : new Error(String(e));
    }
  }, [widget]);

  React.useEffect(() => {
    setError(Component instanceof Error ? Component.message : null);
  }, [Component]);

  React.useEffect(() => {
    const id = setInterval(() => {
      tickRef.current += 1;
    }, clampIntervalMs(intervalMs));
    return () => clearInterval(id);
  }, [intervalMs]);

  const failure = Component instanceof Error ? Component.message : error;

  if (failure) {
    return (
      <div className={className}>
        <pre className="whitespace-pre-wrap rounded-md border border-destructive/50 bg-destructive/15 p-3 font-mono text-xs text-foreground">
          {failure}
        </pre>
      </div>
    );
  }

  const Widget = Component as React.ComponentType<Record<string, unknown>>;

  return (
    <div className={className}>
      <Boundary onError={(e) => setError(e.message)}>
        <Widget {...props} />
      </Boundary>
    </div>
  );
}
