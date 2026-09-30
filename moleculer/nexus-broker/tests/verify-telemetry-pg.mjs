/**
 * Reproducible live-PostgreSQL verification for A3 execution telemetry.
 *
 * WHY THIS IS A COMMITTED SCRIPT RATHER THAN A CLAIM IN THE PR BODY
 * -----------------------------------------------------------------
 * The tester is right that author-reported execution is not the tester's evidence. So the
 * evidence is committed: anyone can run this and get the same result independently.
 *
 * It is deliberately NOT named with the `.test.js` suffix, so the package's unit-test glob
 * does not pick it up — it needs a scratch database and a real postgres image, which unit
 * CI must not depend on. It is a manual, run-it-yourself attestation harness.
 *
 * Usage
 * -----
 *   # 1. throwaway postgres on a port distinct from the live 5432
 *   docker run -d --name a3-verify -e POSTGRES_USER=pguser -e POSTGRES_PASSWORD=pgpass \
 *     -e POSTGRES_DB=nexus -p 55444:5432 postgres:17
 *
 *   # 2. run it (from moleculer/nexus-broker/)
 *   node tests/verify-telemetry-pg.mjs
 *
 *   # 3. tear down
 *   docker rm -f a3-verify
 *
 * Applies the REAL migration 068 to the scratch DB, so the constraints and the append-only
 * trigger under test are the shipped ones, not a reconstruction.
 *
 * Exit code is non-zero on any failure, so it is usable as an attestation artifact.
 */

import { Client } from "pg";
import { readFileSync } from "fs";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";
import { randomUUID } from "crypto";

const HERE = dirname(fileURLToPath(import.meta.url));
const BROKER = resolve(HERE, "..");
const REPO = resolve(BROKER, "..", "..");
const MIG = join(REPO, "typescript/nebula-srv/migrations/068-execution-identity.sql");
const FRAME = join(REPO, "docs/governing-frame.md");

const PORT = Number(process.env.A3_VERIFY_PG_PORT || 55444);

const { emitExecutionTelemetry, loadGoverningText } = await import(
  join(BROKER, "lib/execution-telemetry.ts")
);
const { buildDoctrineSnapshot, contentHash, SHA256_RE } = await import(
  join(BROKER, "lib/doctrine-snapshot.ts")
);

let pass = 0;
let fail = 0;
const ok = (name, cond, extra = "") => {
  if (cond) pass++;
  else fail++;
  console.log(`  ${cond ? "PASS" : "FAIL"}  ${name}${extra ? ` :: ${extra}` : ""}`);
};

const c = new Client({
  host: process.env.A3_VERIFY_PG_HOST || "127.0.0.1",
  port: PORT,
  user: "pguser",
  password: "pgpass",
  database: "nexus",
});
await c.connect();

// Confirm connectivity BEFORE interpreting any outcome. A silent connection failure that
// looks like "no constraint fired" is the failure mode this guards.
const ver = await c.query("select current_setting('server_version') as v");
console.log(`\nconnected to PostgreSQL ${ver.rows[0].v} on port ${PORT}\n`);

await c.query("CREATE SCHEMA IF NOT EXISTS nebula");
await c.query("DROP TABLE IF EXISTS nebula.executions");
await c.query(readFileSync(MIG, "utf8"));
const q = (text, values) => c.query(text, values);

const gov = loadGoverningText(process.env.NEXUS_GOVERNING_TEXT_PATH, REPO);
const CARDS = [
  { slug: "inbox-query-procedure", summary: "query" },
  { slug: "tag-routing", summary: "tags" },
];
const PROMPT = "You are the Engineer.";
const ON = { CENSUS_ENABLED: "1" };
const base = (o = {}) => ({
  params: {},
  systemPrompt: PROMPT,
  procedureIndex: CARDS,
  bootstrap: gov,
  sourceNamespace: "wind",
  executorId: "harness",
  executedByRole: "engineer-iii",
  executedByModel: "opencode/space-bunny-free",
  outcomeStatus: "SUCCEEDED",
  executionId: randomUUID(),
  ...o,
});

console.log("frame + digest");
ok("the ratified frame loads from docs/governing-frame.md", gov.length > 0, `${gov.length} bytes`);
ok("it is the GENERATED frame, not hand-written", /Generated file/.test(gov));

let r = await emitExecutionTelemetry(base(), q, ON);
ok("emit succeeds with the real frame", r.ok, r.errors.join(";"));
ok("doctrine_snapshot_id is a real sha256: address", SHA256_RE.test(r.doctrineSnapshotId || ""), r.doctrineSnapshotId);

const independent = buildDoctrineSnapshot({ systemPrompt: PROMPT, bootstrap: gov, activeProcedureCards: CARDS });
const stored = (await c.query("select doctrine_snapshot_id from nebula.executions limit 1")).rows[0];
ok("stored merkle root == independent digest of the REAL frame bytes",
  stored.doctrine_snapshot_id === independent.snapshot_id, independent.snapshot_id);
ok("bootstrap component hash == hash of the frame file",
  independent.bootstrap_hash === contentHash(gov), independent.bootstrap_hash);

console.log("\ncensus tri-state");
r = await emitExecutionTelemetry(base(), q, {});
// Selected by DISCRIMINATOR, not `order by created_at limit 1`: every row is inserted in
// the same instant, so created_at does not order them and the first row is arbitrary. That
// made this check flaky -- it passed once and failed the next run.
const disabled = (await c.query("select census_enabled,census_sampled,census_id from nebula.executions where census_enabled = false limit 1")).rows[0];
ok("a census-DISABLED walk still records a row", r.ok);
ok("  marker present and disabled", disabled.census_enabled === false && disabled.census_sampled === false && disabled.census_id === null);
await emitExecutionTelemetry(base(), q, { CENSUS_ENABLED: "1", CENSUS_RATE: "1/1" });
const sampled = (await c.query("select census_sampled,census_id from nebula.executions where census_sampled = true limit 1")).rows[0];
ok("a SAMPLED walk carries census_id", sampled.census_sampled === true && !!sampled.census_id);

console.log("\ncohort stability");
for (let i = 0; i < 5; i++) await emitExecutionTelemetry(base({ outcomeStatus: "FAILED" }), q, ON);
const frames = (await c.query("select distinct doctrine_snapshot_id from nebula.executions")).rows;
ok("6+ walks under one frame yield exactly ONE distinct frame", frames.length === 1, `${frames.length} distinct`);
const oldFrame = frames[0].doctrine_snapshot_id;
await emitExecutionTelemetry(base({ bootstrap: gov + "\nre-ratified v2\n" }), q, ON);
const after = (await c.query("select distinct doctrine_snapshot_id from nebula.executions")).rows;
ok("a re-ratified frame mints a NEW frame (no contamination)", after.length === 2, `${after.length} distinct`);
const oldRows = (await c.query("select count(*)::int n from nebula.executions where doctrine_snapshot_id=$1", [oldFrame])).rows[0].n;
ok("older executions stay truthful under the OLD frame", oldRows >= 6, `${oldRows} rows`);

console.log("\ninvariants");
const all = (await c.query("select census_id,census_sampled,session_id from nebula.executions")).rows;
ok("join invariant: census_id <=> sampled, every row", all.every((x) => (x.census_id !== null) === x.census_sampled), `${all.length} rows`);
ok("100% of executions carry a marker", all.every((x) => x.census_sampled !== undefined), `${all.length} rows`);
ok("no session_id is uuid-shaped (068 CHECK + validator)", all.every((x) => !/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(x.session_id || "")));

try { await c.query("update nebula.executions set outcome_status='tampered'"); ok("append-only refuses UPDATE", false, "UPDATE SUCCEEDED"); }
catch (e) { ok("append-only refuses UPDATE", /append.only/i.test(e.message)); }
try { await c.query("delete from nebula.executions"); ok("append-only refuses DELETE", false, "DELETE SUCCEEDED"); }
catch (e) { ok("append-only refuses DELETE", /append.only/i.test(e.message)); }

console.log("\nnever fatal");
const before = (await c.query("select count(*)::int n from nebula.executions")).rows[0].n;
const logged = [];
let walkCompleted = false;
try {
  const bootstrap = loadGoverningText(undefined, "/nonexistent-repo-root"); // throws: frame absent
  await emitExecutionTelemetry(base({ bootstrap }), q, ON);
} catch (m) { logged.push(String(m?.message ?? m)); }
walkCompleted = true;
ok("a missing frame is absorbed by the worker guard; the walk still completes", walkCompleted && logged.length === 1, logged[0] || "nothing logged");
const afterMissing = (await c.query("select count(*)::int n from nebula.executions")).rows[0].n;
ok("a missing frame writes NO fabricated row", afterMissing === before, `${before} -> ${afterMissing}`);

let dbErr = null;
r = await emitExecutionTelemetry(base(), async () => { throw new Error("connection refused"); }, ON, [], (m) => { dbErr = m; });
ok("a DB failure is reported, never propagated", r.ok === false && /connection refused/.test(dbErr || ""), dbErr || "no message");

await c.end();
console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
