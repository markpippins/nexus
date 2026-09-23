import { ConnSpec, DatabaseDiscovery, DbDriver, EngineCapabilities, QueryResult, SchemaDiscovery } from './types';

/**
 * MySQL driver — PROVISIONED STUB (not enabled).
 *
 * The interface is implemented so enablement later is a dependency change,
 * not a code change:
 *   1. `npm install mysql2` in draft-srv
 *   2. Replace the bodies below with mysql2/promise implementations of the
 *      same contract (information_schema discovery maps cleanly; SHOW CREATE
 *      VIEW / triggers live in information_schema.triggers / routines).
 *   3. Flip `available` to true; registry + /api/db/engines pick it up and
 *      the UI can offer it.
 *
 * Until then every call returns a structured NOT_IMPLEMENTED so the UI can
 * grey the engine out with an honest message (fail-visible doctrine).
 */
export class MysqlDriver implements DbDriver {
  readonly capabilities: EngineCapabilities = {
    id: 'mysql',
    label: 'MySQL',
    defaultPort: 3306,
    available: false,
    missingDeps: ['mysql2'],
    supportsDdl: true,
    supportsSchemas: false, // MySQL has databases, not schema-per-database
  };

  private unavailable(): never {
    const err = new Error(
      'MySQL engine is provisioned but not enabled yet. Enablement follow-up: install mysql2 in draft-srv and implement drivers/mysql.ts against a live instance.'
    ) as Error & { code: string };
    err.code = 'ENGINE_NOT_IMPLEMENTED';
    throw err;
  }

  async testConnection(_spec: ConnSpec): Promise<never> {
    this.unavailable();
  }

  async discoverDatabases(_spec: ConnSpec): Promise<{ databases: DatabaseDiscovery[] }> {
    this.unavailable();
  }

  async discoverSchemas(_spec: ConnSpec): Promise<{ schemas: SchemaDiscovery[] }> {
    this.unavailable();
  }

  async execute(_spec: ConnSpec, _sql: string): Promise<QueryResult> {
    this.unavailable();
  }
}
