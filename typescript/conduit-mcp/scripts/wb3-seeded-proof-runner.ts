import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { createRequire } from "node:module";
import { execSync } from "node:child_process";

const require = createRequire(__filename);
const { Pool } = require("pg");

import { initDb, getDb, detectStaleTickets, detectExpiredTickets } from "../src/db";
import {
  buildSweepLogRow,
  TicketExpirySweeper,
  type SweepGenerationInput,
} from "../src/ticket-sweep";

const REPO = path.resolve(__dirname, "../../..");
const OUT = path.join(REPO, "docs/promotion-flow/evidence/wb3-seeded-proof-8261675.json");
const SCRIPT_REL = "typescript/conduit-mcp/scripts/wb3-seeded-proof-runner.ts";
const SCRIPT_ABS = path.join(REPO, SCRIPT_REL);
const WAIVER_REF = "6b42dd3f-ae62-4abf-8cce-fdc01c41032e";
const GRANT_REF = "d77594c2-e266-467e-81d0-dc6ca207c581";
const GRANT_WINDOW_START = Date.parse("2026-11-19T01:19:00Z");
const GRANT_WINDOW_END = Date.parse("2026-11-20T01:19:00Z");
const NEXUS_DSN = process.env.WB3_NEXUS_DSN;
if (!NEXUS_DSN) throw new Error("WB3_NEXUS_DSN (read-only nexus) required");

const SEED_DSN = process.env.CONDUIT_PG_DSN;
if (!SEED_DSN) throw new Error("CONDUIT_PG_DSN (seed) required");
const seedDb = new URL(SEED_DSN).pathname.replace(/^\//, "");
if (!seedDb.startsWith("nexus_wb3_seed_")) {
  throw new Error(`refusing to run: CONDUIT_PG_DSN does not name a nexus_wb3_seed_* database (${seedDb})`);
}

const sha256 = (p: string) => crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
const nowIso = (d: Date | string | null) => (d == null ? null : new Date(d).toISOString());

const PLAN = "WB3SEED";
const PAST = "2026-11-19T00:00:00Z"; // before the sweep
const FUTURE = "2026-11-21T00:00:00Z";
const CASES = [
  { case: "C1", ticket: "ticket-wb3seed-builder-g1", role: "builder", generation: 1, status: "open", expires_at: PAST, expected_disposition: "open_past_expiry_disposed", expected_events: 1 },
  { case: "C2", ticket: "ticket-wb3seed-builder-g2", role: "builder", generation: 2, status: "claimed", expires_at: PAST, expected_disposition: "open_past_expiry_disposed", expected_events: 1 },
  { case: "C3", ticket: "ticket-wb3seed-builder-g3", role: "builder", generation: 3, status: "stale", expires_at: PAST, expected_disposition: "open_past_expiry_disposed", expected_events: 1 },
  { case: "C4", ticket: "ticket-wb3seed-builder-g4", role: "builder", generation: 4, status: "open", expires_at: FUTURE, expected_disposition: "no_disposition_required", expected_events: 0 },
  { case: "C5", ticket: "ticket-wb3seed-reviewer-g1", role: "reviewer", generation: 1, status: "completed", expires_at: PAST, expected_disposition: "no_disposition_required", expected_events: 0 },
  { case: "C6", ticket: "ticket-wb3seed-builder-g5", role: "builder", generation: 5, status: "cancelled", expires_at: PAST, expected_disposition: "no_disposition_required", expected_events: 0 },
  { case: "C7", ticket: "ticket-wb3seed-builder-g6", role: "builder", generation: 6, status: "expired", expires_at: PAST, expected_disposition: "expired_without_event_waived", expected_events: 0 },
  { case: "C8", ticket: "ticket-wb3seed-builder-g7", role: "builder", generation: 7, status: "expired", expires_at: PAST, expected_disposition: "expired_with_event", expected_events: 0 },
  { case: "C9", ticket: "ticket-wb3seed-reviewer-g2", role: "reviewer", generation: 2, status: "open", expires_at: PAST, expected_disposition: "open_past_expiry_disposed", expected_events: 1 },
  { case: "C10", ticket: "ticket-wb3seed-builder-g8", role: "builder", generation: 8, status: "open", expires_at: null, expected_disposition: "no_disposition_required", expected_events: 0 },
] as const;

function poolFor(dsn: string) {
  return new Pool({ connectionString: dsn, max: 3 });
}

async function isolation(pool: any) {
  const one = async (sql: string) => Number((await pool.query(sql)).rows[0].n);
  return {
    vision_tickets_rows: await one(`SELECT count(*) AS n FROM vision.tickets`),
    vision_tickets_expired: await one(`SELECT count(*) AS n FROM vision.tickets WHERE status = 'expired'`),
    kernel_transition_event_expiry_events: await one(
      `SELECT count(*) AS n FROM kernel.transition_event WHERE payload->>'reason' = 'expiry_detection'`,
    ),
    resolution_ticket_rows: await one(`SELECT count(*) AS n FROM resolution.ticket`),
  };
}

async function planProjection(pool: any) {
  return (await pool.query(`SELECT id, title, priority, created_at, updated_at, deleted FROM nebula.plan_status ORDER BY id`)).rows;
}

async function main(): Promise<void> {
  const runAt = new Date();
  if (runAt.getTime() < GRANT_WINDOW_START || runAt.getTime() > GRANT_WINDOW_END) {
    throw new Error(`§9.2 grant window lapsed (${runAt.toISOString()}); no seed DB created by this runner`);
  }
  const nexusPool = poolFor(NEXUS_DSN as string);
  const nexusBefore = await isolation(nexusPool);
  const nexusProjectionBefore = await planProjection(nexusPool);

  const seed = poolFor(SEED_DSN);
  const q = async (sql: string, params?: unknown[]) =>
    (await seed.query(sql, params as never)).rows;

  // Schema bootstrap: migrations must land before the revision is stamped.
  // Migration chatter goes to stderr so stdout stays a machine-readable summary.
  const stdout = console.log.bind(console);
  console.log = (...args: unknown[]) => console.error(...args);
  try {
    await initDb();
  } finally {
    console.log = stdout;
  }

  const schemaRevision = {
    ci_bootstrap_sha256: sha256(`${REPO}/sql/ci-bootstrap/nexus-ci-bootstrap.sql`),
    db_ts_sha256: sha256(`${REPO}/typescript/conduit-mcp/src/db.ts`),
    ticket_sweep_sha256: sha256(`${REPO}/typescript/conduit-mcp/src/ticket-sweep.ts`),
    max_schema_version: Number(
      (await q(`SELECT COALESCE(MAX(version),0) AS n FROM conduit.schema_version`))[0].n,
    ),
  };

  // ── Seed preparation ────────────────────────────────────────────────
  // createSchema() above recreates the partial unique index, so the seed-only
  // relaxation below must follow it.
  // The C1/C10 case pair requires two simultaneously-open builder
  // generations in one plan; the production partial unique index forbids that
  // shape. The index is relaxed in the throwaway seed only — the code path
  // under test never inserts tickets, so this changes no behaviour under test.
  await seed.query(`DROP INDEX IF EXISTS vision.idx_vision_tickets_open`);

  await q(
    `INSERT INTO nebula.blueprints_history (plan_number, title, payload, blueprint_status)
     VALUES ($1, $2, $3::jsonb, 'pending')`,
    [PLAN, "W-B3 seeded proof plan (throwaway)", JSON.stringify({ project: "nexus", goal: "seed fixture", files_affected: [], acceptance_criteria: [], dependencies: [] })],
  );
  await q(
    `INSERT INTO vision.receipts (id, plan_id, type, agent_role, created_at, sequence, summary)
     VALUES ('rcpt-wb3seed-plan-create', $1, 'PLAN_CREATE', 'planner', $2, 0, 'seed fixture')`,
    [PLAN, PAST],
  );
  await q(
    `INSERT INTO vision.receipts (id, plan_id, type, agent_role, created_at, sequence, summary)
     VALUES ('rcpt-wb3seed-review-reject', $1, 'REVIEW_REJECT', 'reviewer', $2, 1, 'C9 predecessor receipt: rework generation')`,
    [PLAN, PAST],
  );

  let minute = 0;
  for (const c of CASES) {
    const createdAt = new Date(Date.parse("2026-11-18T00:00:00Z") + minute * 60_000).toISOString();
    minute += 1;
    await q(
      `INSERT INTO vision.tickets
         (id, plan_id, role, status, created_at, expires_at, closed_at, closure_reason, created_by_receipt, objective)
       VALUES ($1,$2,$3,$4,$5,$6,NULL,NULL,'',$7)`,
      [c.ticket, PLAN, c.role, c.status, createdAt, c.expires_at, `seeded ${c.case}`],
    );
  }

  // C8: an expiry_detection event already exists — the lane must not re-emit.
  await q(
    `INSERT INTO kernel.transition_event
       (event_type, aggregate_type, aggregate_id, actor, authority, payload)
     VALUES ('transition.rejected','ticket',$1,'conduit-mcp','system',$2::jsonb)`,
    [
      "ticket-wb3seed-builder-g7",
      JSON.stringify({
        plan_id: PLAN,
        role: "builder",
        generation: 7,
        expires_at: PAST,
        from_status: "open",
        to_status: "expired",
        reason: "expiry_detection",
        implies_completion: false,
      }),
    ],
  );

  const sweptAt = new Date((await q(`SELECT now() AS n`))[0].n);

  const spineSql = `WITH ranked AS (
      SELECT t.plan_id, t.role, t.id AS ticket_id, t.status, t.created_at, t.expires_at,
             t.closed_at, t.closure_reason,
             row_number() OVER (PARTITION BY t.plan_id, t.role ORDER BY t.created_at, t.id) AS generation,
             count(*) OVER (PARTITION BY t.plan_id, t.role) AS generations_total
      FROM vision.tickets t WHERE t.plan_id IS NOT NULL)
    SELECT plan_id, role, generation, generations_total, ticket_id, status, created_at,
           expires_at, closed_at, closure_reason
    FROM ranked ORDER BY plan_id, role, generation`;

  const chain = "";
  const readState = async () => {
    const rows = (await q(spineSql)) as Array<Record<string, any>>;
    const // keep dexie-free shape
      events = (await q(
        `SELECT aggregate_id,
                count(*)::int AS total,
                count(*) FILTER (WHERE payload->>'reason' = 'expiry_detection')::int AS expiry_events
           FROM kernel.transition_event GROUP BY aggregate_id`,
      )) as Array<{ aggregate_id: string; total: number; expiry_events: number }>;
    const eventMap = new Map(events.map((e) => [e.aggregate_id, e]));
    return rows.map((r) => {
      const e = eventMap.get(r.ticket_id);
      const input: SweepGenerationInput = {
        ticket_id: r.ticket_id,
        plan_id: r.plan_id,
        role: r.role,
        generation: Number(r.generation),
        status: r.status,
        created_at: r.created_at,
        expires_at: r.expires_at,
        closed_at: r.closed_at,
        closure_reason: r.closure_reason,
        has_expiry_event: (e?.expiry_events ?? 0) > 0,
        waiver_ref: r.ticket_id === "ticket-wb3seed-builder-g6" ? WAIVER_REF : null,
      };
      return buildSweepLogRow(input, sweptAt, Number(r.generations_total));
    });
  };

  const preRows = await readState();
  const preProjection = await planProjection(seed);
  const preByTicket = new Map(preRows.map((r) => [r.ticket_id, r]));

  const eventIdsBefore = new Set(
    ((await q(`SELECT event_id::text FROM kernel.transition_event`)) as Array<{ event_id: string }>).map(
      (e) => e.event_id,
    ),
  );

  // ── Implementation under test ───────────────────────────────────────
  const sweeper = new TicketExpirySweeper({ detectStaleTickets, detectExpiredTickets });
  const firstRun = await sweeper.runOnce();

  const postRows = await readState();
  const postProjection = await planProjection(seed);
  const postByTicket = new Map(postRows.map((r) => [r.ticket_id, r]));

  const eventsAfterFirst = (await q(
    `SELECT event_id::text, aggregate_id, payload, timestamp
       FROM kernel.transition_event ORDER BY id`,
  )) as Array<{ event_id: string; aggregate_id: string; payload: any; timestamp: Date }>;

  const secondRun = await sweeper.runOnce();
  const eventsAfterSecond = (await q(
    `SELECT event_id::text, aggregate_id FROM kernel.transition_event ORDER BY id`,
  )) as Array<{ event_id: string; aggregate_id: string }>;
  const postSecondRows = await readState();

  const perCase = CASES.map((c) => {
    const pre = preByTicket.get(c.ticket)!;
    const post = postByTicket.get(c.ticket)!;
    const allForTicket = eventsAfterFirst.filter((e) => e.aggregate_id === c.ticket);
    const emitted = allForTicket.filter((e) => !eventIdsBefore.has(e.event_id));
    const totalAfterSecond = eventsAfterSecond.filter((e) => e.aggregate_id === c.ticket).length;
    const p = emitted[0]?.payload ?? null;
    return {
      case: c.case,
      ticket_id: c.ticket,
      role: c.role,
      derived_generation: pre.generation,
      assumed_generation: c.generation,
      generation_matches: pre.generation === c.generation,
      seeded_status: c.status,
      seeded_expires_at: c.expires_at,
      expected_disposition: c.expected_disposition,
      observed_disposition: pre.sweep_disposition,
      disposition_matches: pre.sweep_disposition === c.expected_disposition,
      expected_events: c.expected_events,
      emitted_events: emitted.length,
      event_count_matches: emitted.length === c.expected_events,
      emitted_event_ids: emitted.map((e) => e.event_id),
      events_total_including_pre_existing: allForTicket.length,
      pre_existing_events: allForTicket.length - emitted.length,
      event_payload: p,
      post_sweep_status: post.status,
      post_sweep_disposition: post.sweep_disposition,
      post_sweep_reason_source: post.reason_source,
      events_total_after_second_run: totalAfterSecond,
      waiver_ref: pre.waiver_ref,
    };
  });

  // C4/C10 window comparisons (A5).
  const c4 = perCase.find((c) => c.case === "C4")!;
  const c10 = perCase.find((c) => c.case === "C10")!;
  const window = {
    C4: {
      expires_at: c4.seeded_expires_at,
      swept_at: sweptAt.toISOString(),
      expires_at_after_swept_at: Date.parse(c4.seeded_expires_at!) > sweptAt.getTime(),
      untouched: c4.post_sweep_status === "open",
      reason: "not due",
    },
    C10: {
      expires_at: null,
      expires_at_is_null: true,
      untouched: c10.post_sweep_status === "open",
      reason: "never due (expires_at IS NULL), distinct from not-yet-due",
    },
  };

  const eightFields = [
    "plan_id",
    "role",
    "generation",
    "expires_at",
    "from_status",
    "to_status",
    "reason",
    "implies_completion",
  ];
  const emittedCases = perCase.filter((c) => c.emitted_events > 0);
  const payloadChecks = emittedCases.map((c) => {
    const p = c.event_payload ?? {};
    return {
      case: c.case,
      fields_present: eightFields.every((f) => Object.prototype.hasOwnProperty.call(p, f)),
      to_status_expired: p.to_status === "expired",
      reason_expiry_detection: p.reason === "expiry_detection",
      implies_completion_is_boolean_false: p.implies_completion === false,
      generation_carried: p.generation === c.derived_generation,
      from_status: p.from_status,
    };
  });

  const dispositionsStable =
    JSON.stringify(postSecondRows.map((r) => [r.ticket_id, r.sweep_disposition])) ===
    JSON.stringify(postRows.map((r) => [r.ticket_id, r.sweep_disposition]));
  const noNewEventsOnSecondRun = eventsAfterSecond.length === eventsAfterFirst.length;

  const completedTickets = (await q(
    `SELECT count(*)::int AS n FROM vision.tickets WHERE status = 'completed'`,
  ))[0].n;
  const planProjectionUnchanged =
    JSON.stringify(preProjection) === JSON.stringify(postProjection);

  const nexusAfter = await isolation(nexusPool);
  const nexusProjectionAfter = await planProjection(nexusPool);

  // ── Teardown ────────────────────────────────────────────────────────
  await seed.end();
  await getDb().end();
  const admin = new Pool({
    connectionString: (() => {
      const u = new URL(SEED_DSN);
      u.pathname = "/postgres";
      return u.toString();
    })(),
    max: 1,
  });
  let teardown: Record<string, unknown>;
  try {
    await admin.query(`DROP DATABASE ${seedDb} WITH (FORCE)`);
    const left = await admin.query(`SELECT count(*)::int AS n FROM pg_database WHERE datname LIKE 'nexus_wb3_seed%'`);
    teardown = { dropped: true, database: seedDb, remaining_seed_databases: left.rows[0].n };
  } catch (e) {
    teardown = { dropped: false, database: seedDb, error: String(e) };
  } finally {
    await admin.end();
    await nexusPool.end();
  }

  const assertions = {
    A1_exact_per_case_counts: {
      pass: perCase.every((c) => c.event_count_matches && c.generation_matches),
      observed: perCase.map((c) => ({
        case: c.case,
        ticket_id: c.ticket_id,
        derived_generation: c.derived_generation,
        emitted_events: c.emitted_events,
        expected_events: c.expected_events,
        emitted_event_ids: c.emitted_event_ids,
        events_total_including_pre_existing: c.events_total_including_pre_existing,
        pre_existing_events: c.pre_existing_events,
      })),
    },
    A2_payload_completeness: {
      pass:
        payloadChecks.length === 4 &&
        payloadChecks.every(
          (p) =>
            p.fields_present &&
            p.to_status_expired &&
            p.reason_expiry_detection &&
            p.implies_completion_is_boolean_false &&
            p.generation_carried,
        ),
      observed: payloadChecks,
    },
    A3_no_completion_implication: {
      pass: completedTickets === 1 && planProjectionUnchanged,
      observed: {
        note:
          "the one completed ticket is C5, seeded completed before the sweep; no ticket moved to completed and " +
          "the receipt-derived plan projection is byte-identical before/after",
        tickets_completed_after_sweep: completedTickets,
        plan_projection_unchanged: planProjectionUnchanged,
        plan_projection_before: preProjection,
      },
    },
    A4_idempotence: {
      pass: noNewEventsOnSecondRun && dispositionsStable && secondRun.expired === 0,
      observed: {
        first_run: firstRun,
        second_run: secondRun,
        events_after_first: eventsAfterFirst.length,
        events_after_second: eventsAfterSecond.length,
        dispositions_stable: dispositionsStable,
      },
    },
    A5_window_respected: {
      pass: window.C4.untouched && window.C10.untouched,
      observed: window,
    },
    A6_one_derivation_two_lanes: {
      pass: perCase.every((c) => c.generation_matches),
      observed: {
        spine: "row_number() OVER (PARTITION BY plan_id, role ORDER BY created_at, id)",
        generations: perCase.map((c) => ({ case: c.case, assumed: c.assumed_generation, derived: c.derived_generation })),
      },
    },
    A7_report_contract_and_isolation: {
      pass:
        Boolean(teardown.dropped) &&
        JSON.stringify(nexusBefore) === JSON.stringify(nexusAfter) &&
        JSON.stringify(nexusProjectionBefore) === JSON.stringify(nexusProjectionAfter),
      observed: {
        seed_database: seedDb,
        schema_revision: schemaRevision,
        seed_script_path: SCRIPT_REL,
        seed_script_sha256: sha256(SCRIPT_ABS),
        run_at: runAt.toISOString(),
        nexus_isolation_before: nexusBefore,
        nexus_isolation_after: nexusAfter,
        nexus_isolation_unchanged: JSON.stringify(nexusBefore) === JSON.stringify(nexusAfter),
        nexus_plan_projection_unchanged:
          JSON.stringify(nexusProjectionBefore) === JSON.stringify(nexusProjectionAfter),
        teardown,
        seed_preparation: [
          "DROP INDEX vision.idx_vision_tickets_open (throwaway seed only): C1 and C10 both require an open " +
            "builder generation in the same plan; the production partial unique index forbids two open rows per " +
            "(plan_id, role). No code path under test inserts tickets, so the drop changes no behaviour under test.",
        ],
      },
    },
  };

  const artifact = {
    artifact: "WB3-SEEDED-PROOF",
    schema: "wb3-seeded-proof/1",
    plan_ref: "8261675",
    work_request_id: process.env.WB3_WORK_REQUEST_ID ?? null,
    attempt_id: process.env.WB3_ATTEMPT_ID ?? null,
    session_id: process.env.WB3_SESSION_ID ?? null,
    grant_ref: GRANT_REF,
    grant_title:
      "Operator grant recorded: §9.2 throwaway seed database for W-B3 (nexus_wb3_seed_<epoch>, window " +
      "2026-11-19T01:19Z→2026-11-20T01:19Z)",
    run_at: runAt.toISOString(),
    seed_database: seedDb,
    credential_handling: "DSN supplied via CONDUIT_PG_DSN only; not recorded in this artifact",
    schema_source:
      "sql/ci-bootstrap/nexus-ci-bootstrap.sql (schema-only repository bootstrap) followed by " +
      "initDb()/createSchema()/runMigrations() from typescript/conduit-mcp/src/db.ts",
    schema_revision: schemaRevision,
    seed_script: {
      path: SCRIPT_REL,
      sha256: sha256(SCRIPT_ABS),
      note: "committed harness; invoked by bin/wb3_seeded_proof.py with CONDUIT_PG_DSN pointing only at the throwaway seed DB",
    },
    implementation_under_test:
      "new TicketExpirySweeper({ detectStaleTickets, detectExpiredTickets }).runOnce() — db.ts detectors, " +
      "ticket-sweep.ts payload builder; no hand-written UPDATE was used to drive the sweep",
    swept_at: sweptAt.toISOString(),
    git_head: execSync("git rev-parse HEAD", { cwd: REPO }).toString().trim(),
    git_branch: execSync("git rev-parse --abbrev-ref HEAD", { cwd: REPO }).toString().trim(),
    cases: perCase,
    assertions,
    no_plan_closure: true,
    no_live_ticket_write: true,
    no_event_synthesis_on_live: true,
    seeded_fixture_event: "C8's expiry_detection event is a fixture inside the throwaway seed DB only; no historical nexus event was manufactured and no event was written to nexus",
    teardown,
  };

  fs.writeFileSync(OUT, JSON.stringify(artifact, null, 2) + "\n");
  console.log(
    JSON.stringify(
      {
        out: OUT,
        seed: seedDb,
        assertions: Object.fromEntries(Object.entries(assertions).map(([k, v]) => [k, v.pass])),
        firstRun,
        secondRun,
        eventsFirst: eventsAfterFirst.length,
        eventsSecond: eventsAfterSecond.length,
        teardown,
      },
      null,
      2,
    ),
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
