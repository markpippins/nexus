import pg from 'pg';
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

dotenv.config({ path: '../../.env' });
dotenv.config({ path: '.env' });

const { Pool } = pg;
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const dsn = process.env.ASSEMBLY_PG_DSN || 'postgresql://pguser:pgpass@localhost:5432/nexus';

export const pool = new Pool({
  connectionString: dsn,
  max: 10,
  idleTimeoutMillis: 30000,
});

pool.on('error', (err) => {
  console.error('Unexpected PostgreSQL pool error', err);
});

export async function query(text, params) {
  const client = await pool.connect();
  try {
    return await client.query(text, params);
  } finally {
    client.release();
  }
}

// ── Migration runner (migrated from assembly-mcp db.ts) ────────────

/**
 * Strip `--` line comments, then split on top-level semicolons.
 *
 * The previous `sql.split(';')` also split *inside* comments. A semicolon in
 * comment prose (e.g. the supervisor seed's `-- administration only; it
 * receives no WorkRequest execution or receipt role.`) chopped the file so
 * that the fragment carrying the real INSERT began with bare English, which
 * then failed with `syntax error at or near "it"`. The supervisor
 * assembly.users row was silently dropped on every boot, and only visible as
 * `failed=3` in the startup log.
 *
 * Comments are removed first, and semicolons inside string literals or
 * quoted identifiers are ignored.
 */
export function splitSqlStatements(sql) {
  const statements = [];
  let current = '';
  let inSingle = false;
  let inDouble = false;
  let inLineComment = false;

  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i];
    const next = sql[i + 1];

    if (inLineComment) {
      if (ch === '\n') {
        inLineComment = false;
        current += ch; // keep newlines so error previews stay readable
      }
      continue;
    }
    if (!inSingle && !inDouble && ch === '-' && next === '-') {
      inLineComment = true;
      i++; // consume the second '-'
      continue;
    }
    if (ch === "'" && !inDouble) {
      if (inSingle && next === "'") {
        current += ch + next; // escaped '' inside a literal
        i++;
        continue;
      }
      inSingle = !inSingle;
    } else if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
    }

    if (ch === ';' && !inSingle && !inDouble) {
      const trimmed = current.trim();
      if (trimmed.length > 0) statements.push(trimmed);
      current = '';
      continue;
    }
    current += ch;
  }

  const tail = current.trim();
  if (tail.length > 0) statements.push(tail);
  return statements;
}

export async function runMigration() {
  const client = await pool.connect();
  try {
    const migrationPath = path.resolve(__dirname, '..', 'assembly-migration.sql');
    if (fs.existsSync(migrationPath)) {
      const sql = fs.readFileSync(migrationPath, 'utf-8');
      const statements = splitSqlStatements(sql);
      let applied = 0, skipped = 0, failed = 0;
      const failures = [];
      for (const stmt of statements) {
        try {
          await client.query(stmt);
          applied++;
        } catch (err) {
          const msg = err.message || '';
          if (msg.includes('already exists')) {
            skipped++; // idempotent — expected on re-runs
          } else {
            failed++;
            failures.push({ stmt, msg });
            console.error(
              '[assembly-srv] Migration statement failed:',
              msg,
              '\n  statement:',
              stmt.replace(/\s+/g, ' ').slice(0, 160)
            );
          }
        }
      }
      console.log(`[assembly-srv] Migration applied from ${migrationPath} (applied=${applied}, skipped=${skipped}, failed=${failed})`);
      if (failed > 0) {
        // A dropped statement is a silent data-loss bug: surface it loudly
        // rather than only in a startup line nobody reads.
        console.error(
          `[assembly-srv] WARNING: ${failed} migration statement(s) did not apply; seeds may be missing.`
        );
      }
    } else {
      console.warn(`[assembly-srv] Migration file not found: ${migrationPath}`);
    }
  } catch (err) {
    console.error('[assembly-srv] Migration runner error:', err.message);
  } finally {
    client.release();
  }
}
