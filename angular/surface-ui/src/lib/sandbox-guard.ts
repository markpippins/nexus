/**
 * sandbox-guard.ts — capability + resource guard for the §10 pasted-code
 * preview sandbox and live data sources.
 *
 * The preview compiles user-pasted JSX/TSX and executes it in the page realm
 * (see WidgetSandbox). This module is the fail-closed gate in front of that
 * execution:
 *
 *   - RESOURCE LIMITS: pasted code size, mock payload serialization size,
 *     and the mock-data tick interval are all bounded.
 *   - SCHEMA VALIDATION: the widget shape (string code, identifier
 *     componentName) is validated before the code is interpolated into a
 *     Function body — a malformed componentName would otherwise be an
 *     injection vector into the compiled source.
 *   - EXPLICIT CAPABILITY GRANTS: the sandbox grants React, the React hooks,
 *     and the mocked fetch/axios stubs and NOTHING else. Pasted code that
 *     references host/browser capabilities outside that grant (window,
 *     document, storage, network transports, code-execution primitives,
 *     frame traversal) is rejected up front with the offending capability
 *     named in the error.
 *
 * This is defense-in-depth for a local preview tool, not a security
 * boundary: static token matching cannot stop every conceivable escape
 * (e.g. `[].constructor.constructor` chains), and a synchronous infinite
 * loop cannot be preempted from the same thread. Those residual risks are
 * tracked with the §10 roadmap; the guards here close the common exfiltration
 * and resource-exhaustion paths.
 */

/** Max characters of pasted component source accepted by the preview. */
export const MAX_CODE_CHARS = 200_000;

/** Max serialized bytes of a single generated mock payload frame. */
export const MAX_MOCK_PAYLOAD_BYTES = 2 * 1024 * 1024;

/** Mock-data tick interval bounds (ms). */
export const MIN_INTERVAL_MS = 100;
export const MAX_INTERVAL_MS = 60_000;

/** Live REST source timeout (ms). */
export const LIVE_FETCH_TIMEOUT_MS = 10_000;

/** Live REST source response body cap (bytes). */
export const MAX_LIVE_RESPONSE_BYTES = 5 * 1024 * 1024;

/**
 * Explicit allowlist of extra origins a live REST adapter source may target.
 * Default is same-origin only; entries must be bare origins
 * (e.g. "https://api.example.com"). Kept empty by default — add entries only
 * for ratified integrations.
 */
export const ALLOWED_LIVE_ORIGINS: string[] = [];

// ---------------------------------------------------------------------------
// capability deny-list
// ---------------------------------------------------------------------------

interface DenyRule {
  re: RegExp;
  capability: string;
  reason: string;
}

/**
 * Escape-hatch tokens. Each names the capability being denied so the error
 * message doubles as the grant documentation. `fetch` / `axios` are NOT
 * denied — the sandbox supplies mocked versions of both as the only network
 * surface.
 */
const DENY_RULES: DenyRule[] = [
  { re: /\bwindow\./g, capability: "window", reason: "host window access" },
  { re: /\bdocument\./g, capability: "document", reason: "host DOM access" },
  { re: /\bglobalThis\b/g, capability: "globalThis", reason: "global scope escape" },
  { re: /\blocalStorage\b/g, capability: "localStorage", reason: "host storage" },
  { re: /\bsessionStorage\b/g, capability: "sessionStorage", reason: "host storage" },
  { re: /\bindexedDB\b/g, capability: "indexedDB", reason: "host storage" },
  { re: /\bXMLHttpRequest\b/g, capability: "XMLHttpRequest", reason: "unmediated network access" },
  { re: /\bWebSocket\b/g, capability: "WebSocket", reason: "unmediated network access" },
  { re: /\bEventSource\b/g, capability: "EventSource", reason: "unmediated network access" },
  { re: /\bnavigator\./g, capability: "navigator", reason: "host environment access" },
  { re: /\bnew\s+Function\b/g, capability: "new Function", reason: "dynamic code execution" },
  { re: /\beval\s*\(/g, capability: "eval", reason: "dynamic code execution" },
  { re: /\bimport\s*\(/g, capability: "import()", reason: "dynamic module loading" },
  { re: /\brequire\s*\(/g, capability: "require", reason: "module loading" },
  { re: /\btop\./g, capability: "top", reason: "frame traversal" },
  { re: /\bparent\./g, capability: "parent", reason: "frame traversal" },
  { re: /\bself\./g, capability: "self", reason: "frame traversal" },
  { re: /\bdocument\.cookie/g, capability: "document.cookie", reason: "credential access" },
];

export interface GuardResult {
  ok: boolean;
  /** Machine-readable capability that triggered the rejection, if any. */
  denied?: string;
  reason?: string;
}

function describe(code: string): GuardResult {
  if (typeof code !== "string") {
    return { ok: false, denied: "widget.code", reason: "widget.code must be a string" };
  }
  if (code.length === 0) {
    return { ok: false, denied: "widget.code", reason: "widget.code is empty" };
  }
  if (code.length > MAX_CODE_CHARS) {
    return {
      ok: false,
      denied: "resource-limit",
      reason: `pasted source exceeds ${MAX_CODE_CHARS} chars (${code.length})`,
    };
  }
  for (const rule of DENY_RULES) {
    rule.re.lastIndex = 0;
    if (rule.re.test(code)) {
      return {
        ok: false,
        denied: rule.capability,
        reason: `capability not granted to sandboxed code: ${rule.capability} (${rule.reason})`,
      };
    }
  }
  return { ok: true };
}

/** Fail-closed gate before compiling pasted widget source. */
export function assertSandboxableCode(code: string): GuardResult {
  return describe(code);
}

const IDENTIFIER_RE = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

/** Validate that a component name is a safe JS identifier before it is
 * interpolated into a Function body. */
export function assertComponentName(name: string): GuardResult {
  if (typeof name !== "string" || !IDENTIFIER_RE.test(name)) {
    return {
      ok: false,
      denied: "componentName",
      reason: `componentName is not a safe identifier: ${JSON.stringify(name)}`,
    };
  }
  return { ok: true };
}

/** Clamp a caller-provided mock tick interval into the allowed range. */
export function clampIntervalMs(intervalMs: number | undefined, fallback = 900): number {
  if (typeof intervalMs !== "number" || !Number.isFinite(intervalMs)) return fallback;
  return Math.min(MAX_INTERVAL_MS, Math.max(MIN_INTERVAL_MS, Math.round(intervalMs)));
}

/**
 * Cap a generated mock payload at MAX_MOCK_PAYLOAD_BYTES of serialized JSON.
 * Returns an error-marker payload instead of handing the widget an
 * unbounded frame.
 */
export function capMockPayload(body: unknown, log?: (line: string) => void): unknown {
  if (body === undefined || body === null) return body;
  const serialized = JSON.stringify(body);
  if (serialized === undefined) return body;
  if (serialized.length <= MAX_MOCK_PAYLOAD_BYTES) return body;
  log?.(`mock payload exceeded ${MAX_MOCK_PAYLOAD_BYTES} bytes — frame truncated to error marker`);
  return {
    error: "mock_payload_limit",
    message: `generated mock payload exceeds ${MAX_MOCK_PAYLOAD_BYTES} bytes`,
    truncated: true,
  };
}
