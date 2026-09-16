import { PayloadSource } from "../types/viewSpec";
import { Adapter, TransformStep } from "./types";
import {
  ALLOWED_LIVE_ORIGINS,
  LIVE_FETCH_TIMEOUT_MS,
  MAX_LIVE_RESPONSE_BYTES,
} from "@/lib/sandbox-guard";

export class AdapterRuntime {
  private registry = new Map<string, Adapter>();

  register(adapter: Adapter): void {
    this.registry.set(adapter.id, adapter);
  }

  getAdapter(id: string): Adapter | undefined {
    return this.registry.get(id);
  }

  async execute(adapterId: string, input?: unknown): Promise<unknown> {
    const adapter = this.registry.get(adapterId);
    if (!adapter) {
      throw new Error(`Adapter not found: ${adapterId}`);
    }

    let data = await this.fetchSource(adapter.source, input);

    for (const step of adapter.steps) {
      data = this.applyTransform(data, step);
    }

    return data;
  }

  private async fetchSource(source: PayloadSource, input?: unknown): Promise<unknown> {
    switch (source.type) {
      case "rest": {
        return this.fetchRestSource(source.url, input);
      }
      case "mock":
        return source.mock ?? input;
      case "sse":
        return input;
      default:
        return input;
    }
  }

  /**
   * Live REST source fetch — fail-closed origin policy, auth boundary,
   * response limits, and timeout/abort (devops hardening).
   *
   * - Origin policy: same-origin URLs only, plus the explicit
   *   ALLOWED_LIVE_ORIGINS allowlist. Cross-origin URLs outside the
   *   allowlist are rejected before any request is made.
   * - Authentication boundary: credentials are never sent ("omit") and URLs
   *   with embedded credentials (user:pass@) are rejected.
   * - Response limits: the body is capped at MAX_LIVE_RESPONSE_BYTES.
   * - Timeout/abort: requests abort after LIVE_FETCH_TIMEOUT_MS.
   * - Schema validation: URL must parse as http(s).
   */
  private async fetchRestSource(url: string | undefined, input?: unknown): Promise<unknown> {
    if (typeof url !== "string" || url.trim() === "") {
      throw new Error(`Adapter rest source missing url (input: ${typeof input})`);
    }

    let parsed: URL;
    try {
      parsed = new URL(url, typeof window !== "undefined" ? window.location.href : undefined);
    } catch {
      throw new Error(`Adapter rest source url is not a valid URL: ${url}`);
    }

    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new Error(`Adapter rest source url must be http(s), got: ${parsed.protocol}//`);
    }

    // Authentication boundary: never embed credentials in the URL.
    if (parsed.username || parsed.password) {
      throw new Error(`Adapter rest source url must not embed credentials`);
    }

    // Origin policy: same-origin by default, plus explicit allowlist.
    const callerOrigin = typeof window !== "undefined" ? window.location.origin : parsed.origin;
    const allowed = parsed.origin === callerOrigin || ALLOWED_LIVE_ORIGINS.includes(parsed.origin);
    if (!allowed) {
      throw new Error(
        `Adapter rest source origin not allowed: ${parsed.origin} (same-origin or ` +
          `explicit ALLOWED_LIVE_ORIGINS entry required)`,
      );
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), LIVE_FETCH_TIMEOUT_MS);
    try {
      const response = await fetch(parsed.toString(), {
        credentials: "omit",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`Adapter rest source responded ${response.status} ${response.statusText}`);
      }
      const contentType = response.headers.get("content-type") ?? "";
      if (!contentType.includes("json") && !contentType.includes("text")) {
        throw new Error(`Adapter rest source returned unexpected content-type: ${contentType}`);
      }
      const text = await response.text();
      if (text.length > MAX_LIVE_RESPONSE_BYTES) {
        throw new Error(
          `Adapter rest source response exceeds ${MAX_LIVE_RESPONSE_BYTES} bytes ` +
            `(${text.length} received)`,
        );
      }
      return JSON.parse(text);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new Error(`Adapter rest source timed out after ${LIVE_FETCH_TIMEOUT_MS}ms`);
      }
      if (error instanceof SyntaxError) {
        throw new Error(`Adapter rest source returned invalid JSON: ${error.message}`);
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  private applyTransform(data: unknown, step: TransformStep): unknown {
    const { op, args = {} } = step;

    switch (op) {
      case "select":
        return this.select(data, (args.path as string) || "");
      case "pluck":
        return Array.isArray(data) ? this.pluck(data, (args.key as string) || "") : data;
      case "filter":
        return Array.isArray(data)
          ? this.filter(data, (args.where as Record<string, unknown>) || {})
          : data;
      case "distinct":
        return Array.isArray(data) ? this.distinct(data, (args.key as string) || "") : data;
      case "map":
        return Array.isArray(data)
          ? this.map(data, (args.fields as Record<string, string>) || {})
          : data;
      case "groupBy":
        return Array.isArray(data) ? this.groupBy(data, (args.key as string) || "") : data;
      case "count":
        return Array.isArray(data) ? data.length : 0;
      case "countBy":
        return Array.isArray(data) ? this.countBy(data, (args.key as string) || "") : data;
      case "sortBy":
        return Array.isArray(data)
          ? this.sortBy(
              data,
              (args.key as string) || "",
              (args.direction as "asc" | "desc") || "asc",
            )
          : data;
      case "flatten":
        return Array.isArray(data) ? this.flatten(data) : data;
      case "coalesce":
        return this.coalesce(data, (args.fields as string[]) || []);
      case "default":
        return data !== undefined && data !== null && data !== "" ? data : args.value;
      case "format":
        return this.format(data, (args.type as string) || "", (args.format as string) || "");
      case "semanticMap":
        return this.semanticMap(
          data,
          (args.field as string) || "",
          (args.map as Record<string, unknown>) || {},
        );
      default:
        return data;
    }
  }

  private select(data: unknown, path: string): unknown {
    const parts = path.split(".");
    let current: unknown = data;
    for (const part of parts) {
      if (current && typeof current === "object" && part in (current as Record<string, unknown>)) {
        current = (current as Record<string, unknown>)[part];
      } else {
        return undefined;
      }
    }
    return current;
  }

  private pluck(data: unknown[], key: string): unknown[] {
    return data.map((item) =>
      item && typeof item === "object" ? (item as Record<string, unknown>)[key] : undefined,
    );
  }

  private filter(data: unknown[], where: Record<string, unknown>): unknown[] {
    return data.filter((item) => {
      return Object.entries(where).every(([key, value]) => {
        const actual = this.select(item, key);
        if (value && typeof value === "object" && "$regex" in (value as Record<string, unknown>)) {
          return new RegExp(String((value as Record<string, unknown>).$regex)).test(String(actual));
        }
        return actual === value;
      });
    });
  }

  private distinct(data: unknown[], key: string): unknown[] {
    const seen = new Set();
    return data.filter((item) => {
      const value = this.select(item, key);
      if (seen.has(value)) return false;
      seen.add(value);
      return true;
    });
  }

  private map(data: unknown[], fields: Record<string, string>): unknown[] {
    return data.map((item) => {
      const result: Record<string, unknown> = {};
      for (const [targetKey, sourcePath] of Object.entries(fields)) {
        result[targetKey] = this.select(item, sourcePath);
      }
      return result;
    });
  }

  private groupBy(
    data: unknown[],
    key: string,
  ): { groups: Array<{ key: string; items: unknown[] }> } {
    const groups = new Map<string, unknown[]>();
    for (const item of data) {
      const groupKey = String(this.select(item, key));
      if (!groups.has(groupKey)) {
        groups.set(groupKey, []);
      }
      groups.get(groupKey)!.push(item);
    }
    return {
      groups: Array.from(groups.entries()).map(([k, items]) => ({ key: k, items })),
    };
  }

  private countBy(data: unknown[], key: string): Record<string, number> {
    const result: Record<string, number> = {};
    for (const item of data) {
      const countKey = String(this.select(item, key));
      result[countKey] = (result[countKey] || 0) + 1;
    }
    return result;
  }

  private sortBy(data: unknown[], key: string, direction: "asc" | "desc" = "asc"): unknown[] {
    return [...data].sort((a, b) => {
      const aVal = String(this.select(a, key) ?? "");
      const bVal = String(this.select(b, key) ?? "");
      const comparison = aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
      return direction === "asc" ? comparison : -comparison;
    });
  }

  private flatten(data: unknown[]): unknown[] {
    return data.flat();
  }

  private coalesce(data: unknown, fields: string[]): unknown {
    for (const field of fields) {
      const value = this.select(data, field);
      if (value !== undefined && value !== null && value !== "") {
        return value;
      }
    }
    return undefined;
  }

  private format(data: unknown, type: string, fmt: string): unknown {
    if (type === "date") {
      const date = new Date(String(data));
      if (fmt === "iso") return date.toISOString();
      if (fmt === "locale") return date.toLocaleString();
      return date;
    }
    if (type === "number") {
      const num = Number(data);
      if (fmt === "currency") return `$${num.toFixed(2)}`;
      if (fmt === "percent") return `${(num * 100).toFixed(1)}%`;
      if (fmt === "compact") {
        if (num >= 1e6) return `${(num / 1e6).toFixed(1)}M`;
        if (num >= 1e3) return `${(num / 1e3).toFixed(1)}K`;
        return num;
      }
      return num;
    }
    return data;
  }

  private semanticMap(data: unknown, field: string, map: Record<string, unknown>): unknown {
    if (Array.isArray(data)) {
      return data.map((item) => {
        const value = String(this.select(item, field));
        return {
          ...(typeof item === "object" && item !== null ? item : {}),
          [field]: map[value] || value,
        };
      });
    }
    const value = String(this.select(data, field));
    return map[value] || value;
  }
}
