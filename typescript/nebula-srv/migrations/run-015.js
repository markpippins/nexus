// Migration 015 runner: Harvest analytics stat columns + sort indexes
// Run: node migrations/run-015.js
// Companion SQL: migrations/015-harvest-stats-columns.sql (also psql-able)
//
// Idempotent: IF NOT EXISTS on columns/indexes; the view is replaced with the
// full definition. Safe to re-run.

const { Pool } = require('pg');
const fs = require('fs');
const path = require('path');

const pool = new Pool({
  connectionString: process.env.CONDUIT_PG_DSN || 'postgresql://pguser:pgpass@localhost:5432/nexus',
});

const SQL_FILE = path.join(__dirname, '015-harvest-stats-columns.sql');

async function check(label, fn) {
  const ok = await fn();
  console.log(`  ${ok ? '✓' : '✗'} ${label}`);
  return ok;
}

async function main() {
  console.log('Running Migration 015: Harvest analytics stat columns...\n');

  // Prerequisites
  const okTable = await check('nebula.harvests_history exists', async () => {
    const r = await pool.query(`SELECT to_regclass('nebula.harvests_history') AS t`);
    return r.rows[0].t !== null;
  });
  const okView = await check('nebula.harvests view exists', async () => {
    const r = await pool.query(`SELECT to_regclass('nebula.harvests') AS t`);
    return r.rows[0].t !== null;
  });
  if (!okTable || !okView) {
    throw new Error('Prerequisites missing — run scd-type4-temporal migrations first.');
  }

  // jsonb_path_query_array must be IMMUTABLE for the generated column (PG16+).
  const vol = await pool.query(
    `SELECT provolatile FROM pg_proc
     WHERE proname = 'jsonb_path_query_array' AND pronamespace = 'pg_catalog'::regnamespace`
  );
  if (vol.rows.length && vol.rows[0].provolatile !== 'i') {
    throw new Error(
      `jsonb_path_query_array is ${vol.rows[0].provolatile.toUpperCase()} on this build — ` +
      `stats_user_turns needs an IMMUTABLE wrapper. See migration SQL header.`
    );
  }
  console.log('  ✓ jsonb_path_query_array is IMMUTABLE');

  const already = await pool.query(
    `SELECT count(*)::int AS n FROM information_schema.columns
     WHERE table_schema='nebula' AND table_name='harvests_history'
       AND column_name IN ('stats_turns','stats_user_turns','stats_code_blocks','stats_block_density')`
  );
  if (already.rows[0].n === 4) {
    console.log('\nAll four stat columns already present — verifying artifacts only.\n');
  }

  const sql = fs.readFileSync(SQL_FILE, 'utf8');
  await pool.query(sql);

  // Post-verification
  const cols = await pool.query(
    `SELECT column_name FROM information_schema.columns
     WHERE table_schema='nebula' AND table_name='harvests_history'
       AND column_name IN ('stats_turns','stats_user_turns','stats_code_blocks','stats_block_density')`
  );
  if (cols.rows.length !== 4) {
    throw new Error(`Expected 4 stat columns, found ${cols.rows.length}`);
  }
  console.log('  ✓ 4 generated stat columns present');

  const viewCols = await pool.query(
    `SELECT count(*)::int AS n FROM information_schema.columns
     WHERE table_schema='nebula' AND table_name='harvests'
       AND column_name LIKE 'stats_%'`
  );
  if (viewCols.rows[0].n !== 4) {
    throw new Error(`View exposes ${viewCols.rows[0].n} stats columns, expected 4`);
  }
  console.log('  ✓ view exposes all 4 stat columns');

  const idx = await pool.query(
    `SELECT count(*)::int AS n FROM pg_indexes
     WHERE schemaname='nebula' AND tablename='harvests_history'
       AND indexname IN ('idx_hh_stats_turns','idx_hh_stats_user_turns','idx_hh_stats_code_blocks','idx_hh_stats_block_density')`
  );
  if (idx.rows[0].n !== 4) {
    throw new Error(`Expected 4 stat indexes, found ${idx.rows[0].n}`);
  }
  console.log('  ✓ 4 (stat DESC NULLS LAST, id DESC) indexes present');

  // Planner check: the sort pathkey must be driven by the index through the view.
  const plan = await pool.query(
    `EXPLAIN (FORMAT JSON) SELECT h.id FROM nebula.harvests h
     ORDER BY h.stats_turns DESC NULLS LAST, h.id DESC LIMIT 100`
  );
  const planStr = JSON.stringify(plan.rows[0]);
  if (!planStr.includes('idx_hh_stats_turns')) {
    throw new Error('Planner did not pick idx_hh_stats_turns for the turns sort — check statistics');
  }
  console.log('  ✓ planner drives the turns sort via idx_hh_stats_turns');

  const sample = await pool.query(
    `SELECT count(*)::int AS n, max(stats_turns) AS max_turns, max(stats_user_turns) AS max_user_turns
     FROM nebula.harvests`
  );
  console.log(
    `  ✓ view serves ${sample.rows[0].n} rows (max stats_turns=${sample.rows[0].max_turns}, max stats_user_turns=${sample.rows[0].max_user_turns})`
  );

  console.log('\nMigration 015 complete.');
  console.log('Follow-up (nebula-srv routes.ts, engineer per record 46b8a0f5):');
  console.log('  wire sortExpr for turns/block_density/collaboration/code_blocks to h.stats_* columns.');
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error('\nMigration 015 FAILED:', err.message);
    process.exit(1);
  })
  .finally(() => pool.end());
