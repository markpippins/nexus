/**
 * Driver abstraction — the multi-database provisioning surface.
 *
 * data-explorer-ui speaks a fixed JSON contract (SchemaObject[] / query
 * results, see angular/data-explorer-ui/src/types/database.ts). Each engine
 * implements that contract behind this interface so the UI never learns
 * dialects. Adding MySQL later = implement DbDriver + flip registry.available;
 * no route or frontend changes.
 */

export interface ConnSpec {
  host?: string;
  port?: number | string;
  database?: string;
  username?: string;
  password?: string;
  ssl?: boolean;
}

export interface ColumnInfo {
  name: string;
  type: string;
  isPrimaryKey?: boolean;
  isNullable?: boolean;
  isForeignKey?: boolean;
  referencesTable?: string;
  referencesColumn?: string;
  defaultValue?: string;
  comment?: string;
}

export interface DatabaseDiscovery {
  name: string;
  allowConnections: boolean;
  isTemplate: boolean;
}

export interface SchemaDiscovery {
  name: string;
  tables: Array<{
    name: string;
    schema: string;
    rowCount: number;
    columns: ColumnInfo[];
    indexes?: Array<{ name: string; columns: string[]; isUnique: boolean }>;
    comment?: string;
  }>;
  views: Array<{ name: string; schema: string; definition: string; comment?: string }>;
  triggers: Array<{
    name: string;
    schema: string;
    tableName: string;
    timing: string;
    event: string;
    functionName: string;
    definition: string;
  }>;
  procedures: Array<{
    name: string;
    schema: string;
    returnType: string;
    parameters: { name: string; type: string }[];
    definition: string;
    comment?: string;
  }>;
}

export interface QueryResult {
  columns: string[];
  columnTypes?: Record<string, string>;
  rows: Record<string, any>[];
  rowCount: number;
  affectedRows?: number | null;
  executionTimeMs: number;
  status: 'success' | 'error';
  error?: string;
  message?: string;
  timestamp: string;
}

export interface EngineCapabilities {
  id: string;
  label: string;
  defaultPort: number;
  /** false until the driver dependency is installed and integration-tested */
  available: boolean;
  /** populated when available=false — what stands between here and enablement */
  missingDeps: string[];
  supportsDdl: boolean;
  supportsSchemas: boolean;
}

export interface DbDriver {
  readonly capabilities: EngineCapabilities;
  testConnection(spec: ConnSpec): Promise<{
    success: boolean;
    message: string;
    latencyMs: number;
    version?: string;
    database?: string;
  }>;
  discoverDatabases(spec: ConnSpec): Promise<{ databases: DatabaseDiscovery[] }>;
  discoverSchemas(spec: ConnSpec): Promise<{ schemas: SchemaDiscovery[] }>;
  execute(spec: ConnSpec, sql: string): Promise<QueryResult>;
}
