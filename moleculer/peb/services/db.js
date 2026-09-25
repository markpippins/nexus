import pg from 'pg';
import dotenv from 'dotenv';

dotenv.config({ path: '../../.env' });
dotenv.config({ path: '.env' });

const { Pool } = pg;

// peb lives in the *nexus* database, not postgres. shrapnel & nebula each have
// their own; PEB observes kernel/agent transactions persisted into nexus.peb.
const dsn =
  process.env.PEB_PG_DSN ||
  'postgresql://pguser:pgpass@localhost:5432/nexus';

export const pool = new Pool({
  connectionString: dsn,
  max: 10,
  idleTimeoutMillis: 30000,
});

pool.on('error', (err) => {
  console.error('[peb-srv] Unexpected PostgreSQL pool error', err);
});

export async function query(text, params) {
  const client = await pool.connect();
  try {
    return await client.query(text, params);
  } finally {
    client.release();
  }
}

export async function withTransaction(fn) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    try { await client.query('ROLLBACK'); } catch (_) {}
    throw err;
  } finally {
    client.release();
  }
}

export const dsnInfo = dsn.replace(/:[^:@/]+@/, ':***@');
