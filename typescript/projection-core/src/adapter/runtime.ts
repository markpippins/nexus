import { PayloadSource } from "../types/viewSpec";
import { Adapter, TransformStep } from "./types";
import { FetchGovernedSourceClient, assertGovernedSource } from "./governed";

export class AdapterRuntime {
  private registry = new Map<string, Adapter>();
  private readonly governedSourceClient = new FetchGovernedSourceClient();

  register(adapter: Adapter): void {
    if (!adapter.id || !adapter.source || !Array.isArray(adapter.steps)) {
      throw new Error("Invalid adapter definition");
    }
    this.registry.set(adapter.id, adapter);
  }

  getAdapter(id: string): Adapter | undefined {
    return this.registry.get(id);
  }

  async execute(adapterId: string, input?: any): Promise<any> {
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

  private async fetchSource(source: PayloadSource, input?: any): Promise<any> {
    switch (source.type) {
      case "server": {
        assertGovernedSource(source);
        const projection = await this.governedSourceClient.fetchProjection(source, undefined);
        return projection.data;
      }
      case "rest": {
        if (!source.url) throw new Error("REST adapter source URL is required");
        const response = await fetch(source.url);
        if (!response.ok) throw new Error(`Adapter source failed: HTTP ${response.status}`);
        return response.json();
      }
      case "mock":
        return source.mock ?? input;
      case "sse":
        return input;
      case "file":
      case "agent":
        throw new Error(`Unsupported live adapter source: ${source.type}`);
      default:
        throw new Error(`Unsupported adapter source: ${source.type}`);
    }
  }

  private applyTransform(data: any, step: TransformStep): any {
    const { op, args } = step;

    switch (op) {
      case "select":
        return this.select(data, args!.path);
      case "pluck":
        return this.pluck(data, args!.key);
      case "filter":
        return this.filter(data, args!.where);
      case "distinct":
        return this.distinct(data, args!.key);
      case "map":
        return this.map(data, args!.fields);
      case "groupBy":
        return this.groupBy(data, args!.key);
      case "count":
        return Array.isArray(data) ? data.length : 0;
      case "countBy":
        return this.countBy(data, args!.key);
      case "sortBy":
        return this.sortBy(data, args!.key, args!.direction);
      case "flatten":
        return this.flatten(data);
      case "coalesce":
        return this.coalesce(data, args!.fields);
      case "default":
        return data !== undefined && data !== null && data !== ""
          ? data
          : args!.value;
      case "format":
        return this.format(data, args!.type, args!.format);
      case "semanticMap":
        return this.semanticMap(data, args!.field, args!.map);
      default:
        return data;
    }
  }

  private select(data: any, path: string): any {
    const parts = path.split(".");
    let current = data;
    for (const part of parts) {
      if (current && typeof current === "object" && part in current) {
        current = current[part];
      } else {
        return undefined;
      }
    }
    return current;
  }

  private pluck(data: any[], key: string): any[] {
    return data.map((item) => item[key]);
  }

  private filter(data: any[], where: Record<string, any>): any[] {
    return data.filter((item) => {
      return Object.entries(where).every(([key, value]) => {
        const actual = this.select(item, key);
        if (value && typeof value === "object" && "$regex" in value) {
          return new RegExp(value.$regex).test(actual);
        }
        return actual === value;
      });
    });
  }

  private distinct(data: any[], key: string): any[] {
    const seen = new Set();
    return data.filter((item) => {
      const value = this.select(item, key);
      if (seen.has(value)) return false;
      seen.add(value);
      return true;
    });
  }

  private map(data: any[], fields: Record<string, string>): any[] {
    return data.map((item) => {
      const result: Record<string, any> = {};
      for (const [targetKey, sourcePath] of Object.entries(fields)) {
        result[targetKey] = this.select(item, sourcePath);
      }
      return result;
    });
  }

  private groupBy(data: any[], key: string): { groups: Array<{ key: string; items: any[] }> } {
    const groups = new Map<string, any[]>();
    for (const item of data) {
      const groupKey = String(this.select(item, key));
      if (!groups.has(groupKey)) {
        groups.set(groupKey, []);
      }
      groups.get(groupKey)!.push(item);
    }
    return {
      groups: Array.from(groups.entries()).map(([key, items]) => ({ key, items })),
    };
  }

  private countBy(data: any[], key: string): Record<string, number> {
    const result: Record<string, number> = {};
    for (const item of data) {
      const countKey = String(this.select(item, key));
      result[countKey] = (result[countKey] || 0) + 1;
    }
    return result;
  }

  private sortBy(data: any[], key: string, direction: "asc" | "desc" = "asc"): any[] {
    return [...data].sort((a, b) => {
      const aVal = this.select(a, key);
      const bVal = this.select(b, key);
      const comparison = aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
      return direction === "asc" ? comparison : -comparison;
    });
  }

  private flatten(data: any[]): any[] {
    return data.flat();
  }

  private coalesce(data: any, fields: string[]): any {
    for (const field of fields) {
      const value = this.select(data, field);
      if (value !== undefined && value !== null && value !== "") {
        return value;
      }
    }
    return undefined;
  }

  private format(data: any, type: string, fmt: string): any {
    if (type === "date") {
      const date = new Date(data);
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

  private semanticMap(data: any, field: string, map: Record<string, any>): any {
    if (Array.isArray(data)) {
      return data.map((item) => {
        const value = this.select(item, field);
        return {
          ...item,
          [field]: map[value] || value,
        };
      });
    }
    const value = this.select(data, field);
    return map[value] || value;
  }
}
