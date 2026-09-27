import pg from 'pg';
import dotenv from 'dotenv';

dotenv.config({ path: '../../.env' });
dotenv.config({ path: '.env' });

const { Pool } = pg;

// The aegis schema lives in the `nexus` database alongside nebula, cascade,
// etc. Env overrides mirror nebula-srv defaults for container deploys.
const pool = new Pool({
  host: process.env.PG_HOST || 'localhost',
  port: parseInt(process.env.PG_PORT || '5432', 10),
  user: process.env.PG_USER || 'pguser',
  password: process.env.PG_PASSWORD || process.env.PG_PASS || 'pgpass',
  database: process.env.PG_DB_NAME || 'nexus',
  options: '-c search_path=aegis',
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

pool.on('error', (err) => {
  console.error('[aegis-srv] Unexpected PostgreSQL pool error', err);
});

export async function query(text: string, params?: any[]) {
  const client = await pool.connect();
  try {
    return await client.query(text, params);
  } finally {
    client.release();
  }
}

export async function withTransaction<T>(fn: (client: pg.PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

export { pool };