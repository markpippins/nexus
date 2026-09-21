#!/usr/bin/env node
// Idempotently applies every SQL file under ./migrations/*.sql in sorted order.
import pg from 'pg';
import dotenv from 'dotenv';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

dotenv.config({ path: '../../.env' });
dotenv.config({ path: '.env' });

const __dirname = dirname(fileURLToPath(import.meta.url));
const migrationsDir = join(__dirname, '..', '..', 'migrations');

const dsn = process.env.SHRAPNEL_PG_DSN || 'postgresql://pguser:pgpass@localhost:5432/postgres';
const { Pool } = pg;
const pool = new Pool({ connectionString: dsn });

async function main() {
  const files = readdirSync(migrationsDir).filter(f => f.endsWith('.sql')).sort();
  console.log(`[shrapnel migrate] found ${files.length} migration file(s): ${files.join(', ')}`);

  // Ensure a migrations ledger exists (the shrapnel schema itself is created
  // by 0001_init.sql, so bootstrap it first on brand-new databases).
  await pool.query(`CREATE SCHEMA IF NOT EXISTS shrapnel AUTHORIZATION pguser`);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS shrapnel._migration_ledger (
      filename     text PRIMARY KEY,
      applied_at   timestamptz NOT NULL DEFAULT now()
    )
  `);

  for (const file of files) {
    const led = await pool.query(
      `SELECT filename FROM shrapnel._migration_ledger WHERE filename = $1`,
      [file]
    );
    if (led.rowCount > 0) {
      console.log(`[shrapnel migrate] skip ${file} (already applied)`);
      continue;
    }
    const sql = readFileSync(join(migrationsDir, file), 'utf8');
    await pool.query('BEGIN');
    try {
      await pool.query(sql);
      await pool.query(
        `INSERT INTO shrapnel._migration_ledger (filename) VALUES ($1)`,
        [file]
      );
      await pool.query('COMMIT');
      console.log(`[shrapnel migrate] applied ${file}`);
    } catch (err) {
      await pool.query('ROLLBACK').catch(() => {});
      console.error(`[shrapnel migrate] FAILED ${file}:`, err.message);
      process.exit(1);
    }
  }
  console.log('[shrapnel migrate] done');
  await pool.end();
}

main().catch((err) => {
  console.error('[shrapnel migrate] unhandled', err);
  process.exit(1);
});
