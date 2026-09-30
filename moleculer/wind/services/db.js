import pg from 'pg';
import dotenv from 'dotenv';

dotenv.config({ path: '../../.env' });
dotenv.config({ path: '.env' });

const { Pool } = pg;

const dsn = process.env.WIND_PG_DSN || process.env.NEXUS_PG_DSN || 'postgresql://pguser:pgpass@localhost:5432/nexus';

export const pool = new Pool({
  connectionString: dsn,
  max: 10,
  idleTimeoutMillis: 30000,
});

pool.on('error', (err) => {
  console.error('[wind-srv] Unexpected PostgreSQL pool error', err);
});

export async function query(text, params) {
  const client = await pool.connect();
  try {
    return await client.query(text, params);
  } finally {
    client.release();
  }
}
