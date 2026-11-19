/**
 * One-shot migration: import builder-log/*.meta.txt into sessions table.
 * Run with: npx tsx scripts/migrate-sessions.ts
 */
import fs from 'fs';
import path from 'path';
import Database from 'better-sqlite3';

const BASE_DIR = '/home/codex/dev/nexus/.conduit-data';
const LOG_DIR = path.join(BASE_DIR, 'IMPLEMENTATION_PLANS', 'builder-log');

function main() {
  const db = new Database(path.join(BASE_DIR, 'pipeline.db'));
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');

  // Ensure sessions table exists (idempotent — db.ts createSchema handles this)
  db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      id              TEXT PRIMARY KEY,
      agent_role      TEXT NOT NULL,
      start_iso       TEXT NOT NULL,
      end_iso         TEXT,
      exit_code       INTEGER,
      retries_used    INTEGER DEFAULT 0,
      plans_processed TEXT NOT NULL DEFAULT '[]',
      plan_count      INTEGER DEFAULT 0,
      pid             INTEGER,
      is_running      INTEGER DEFAULT 1,
      last_activity   TEXT,
      model           TEXT,
      fallback_used   INTEGER DEFAULT 0,
      created_at      TEXT NOT NULL
    );
  `);

  const insert = db.prepare(`
    INSERT OR IGNORE INTO sessions
      (id, agent_role, start_iso, end_iso, exit_code, retries_used,
       plans_processed, plan_count, pid, is_running, last_activity, created_at)
    VALUES (@id, @agent_role, @start_iso, @end_iso, @exit_code, @retries_used,
            @plans_processed, @plan_count, @pid, 0, @end_iso, @start_iso)
  `);

  let migrated = 0;
  let skipped = 0;

  const files = fs.readdirSync(LOG_DIR).filter(
    (f) => f.endsWith('.meta.txt') && f !== 'latest.meta.txt',
  );

  for (const file of files.sort()) {
    const filePath = path.join(LOG_DIR, file);
    const content = fs.readFileSync(filePath, 'utf-8');
    const fields: Record<string, string> = {};
    for (const line of content.split('\n')) {
      const eq = line.indexOf('=');
      if (eq > 0) {
        fields[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
      }
    }

    const sid = fields['SESSION_ID'];
    if (!sid) {
      skipped++;
      continue;
    }

    // Parse plans field: space-separated plan IDs
    const plansStr = fields['PLANS'] || '';
    const plans = plansStr ? plansStr.split(/\s+/) : [];

    try {
      insert.run({
        id: sid,
        agent_role: 'builder',
        start_iso: fields['START_ISO'] || '',
        end_iso: fields['END_ISO'] || null,
        exit_code: fields['EXIT_CODE'] ? parseInt(fields['EXIT_CODE'], 10) : null,
        retries_used: fields['RETRIES_USED'] ? parseInt(fields['RETRIES_USED'], 10) : 0,
        plans_processed: JSON.stringify(plans),
        plan_count: fields['PLAN_COUNT'] ? parseInt(fields['PLAN_COUNT'], 10) : 0,
        pid: null,
      });
      migrated++;
    } catch (err: any) {
      if (err.code === 'SQLITE_CONSTRAINT_PRIMARYKEY') {
        console.log(`  (skip: ${sid} already exists)`);
        skipped++;
      } else {
        console.error(`  FAILED ${sid}: ${err.message}`);
        skipped++;
      }
    }
  }

  const count = db.prepare('SELECT COUNT(*) as c FROM sessions').get() as {
    c: number;
  };
  console.log(`\nMigration complete: ${migrated} imported, ${skipped} skipped`);
  console.log(`Total sessions in DB: ${count.c}`);

  // Show last 5
  const rows = db
    .prepare('SELECT id, exit_code, plan_count FROM sessions ORDER BY start_iso DESC LIMIT 5')
    .all() as any[];
  console.log('\nLast 5 sessions:');
  for (const r of rows) {
    console.log(`  ${r.id}  exit=${r.exit_code}  plans=${r.plan_count}`);
  }

  db.close();
}

main();
