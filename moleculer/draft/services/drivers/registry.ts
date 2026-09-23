import { DbDriver, EngineCapabilities } from './types';
import { PostgresDriver } from './postgres';
import { MysqlDriver } from './mysql';

/**
 * Engine catalog. An engine is `available: true` only when its driver module
 * is installed AND integration-tested against a live instance. MySQL ships as
 * a provisioned stub: interface complete, dependency intentionally absent
 * until the enablement follow-up (see drivers/mysql.ts header).
 */

const postgres = new PostgresDriver();
const mysql = new MysqlDriver();

const DRIVERS: Record<string, DbDriver> = {
  postgres,
  mysql,
};

export function getDriver(engineId: string | undefined): DbDriver | null {
  if (!engineId) return DRIVERS.postgres; // default engine (UI contract today)
  return DRIVERS[engineId] ?? null;
}

export function listCapabilities(): EngineCapabilities[] {
  return Object.values(DRIVERS).map((d) => d.capabilities);
}

export function isEngineAvailable(engineId: string | undefined): boolean {
  const d = getDriver(engineId);
  return !!d?.capabilities.available;
}
