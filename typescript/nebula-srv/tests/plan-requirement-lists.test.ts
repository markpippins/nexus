/**
 * P4a: a Blueprint must carry the full set of Requirements and Specifications
 * it implements — not only the singular `requirementRef` / `specRef` pair.
 *
 * Before this change `POST /api/plans` destructured a fixed body and ignored
 * every other key, so `requirement_ids` / `specification_ids` were dropped on
 * the floor: the endpoint answered 201 and the ids were nowhere — not in the
 * blueprint payload, not on any read, not in any error. This script pins the
 * replacement behaviour against a real server and a real (throwaway) database,
 * because the defect was a missing persistence path, not a missing string.
 *
 * Asserts:
 *   1. both lists round-trip: 201 -> payload -> GET /api/plans/:id -> GET /api/plans
 *   2. every rejection (unknown id, wrong entity type, out-of-scope
 *      specification) is a 400 naming the field, and leaves NO new blueprint
 *      row and NO blueprint payload containing the rejected id — asserted by
 *      counting rows before/after, not inferred
 *   3. the same endpoint keeps working once V222 ports `nebula.requirements`
 *      into `resolution.requirement`: a plan created before the port still
 *      resolves its ids after it (apply V222 in-test with P4A_APPLY_V222=1),
 *      and the script also runs unchanged against a database where V222 was
 *      applied before the server booted (fixtures are seeded through whichever
 *      relation `nebula.requirements` currently is)
 *   4. a blueprint that carries only the singular refs keeps its old shape
 *
 * Usage: npx tsx tests/plan-requirement-lists.test.ts
 *        NEBULA_TEST_BASE=http://127.0.0.1:<port> PG_DB_NAME=<throwaway db> (non-default server)
 *        P4A_APPLY_V222=1   apply V220+V222 mid-run (throwaway DB only)
 *
 * PG_DB_NAME MUST name the throwaway database the server is booted against —
 * the fixture writes go to that database. Refusing to run without it is
 * deliberate: on this host the pool's default database is the live one.
 */
import * as http from "http";
import * as path from "path";
import { spawnSync } from "child_process";
import { Pool } from "pg";

const BASE = process.env.NEBULA_TEST_BASE || "http://localhost:3101";
const REPO_ROOT = process.env.REPO_ROOT || path.resolve(process.cwd(), "..", "..");

const DB_NAME = process.env.PG_DB_NAME || "";
if (!DB_NAME || DB_NAME === "nexus") {
  console.error(
    "FAIL: set PG_DB_NAME to the throwaway database this server runs against " +
      "(refusing to write fixtures into the default/live database)"
  );
  process.exit(1);
}
if (process.env.P4A_APPLY_V222 && process.env.P4A_APPLY_V222 !== "1") {
  console.error("FAIL: P4A_APPLY_V222, when set, must be 1");
  process.exit(1);
}
const APPLY_V222 = process.env.P4A_APPLY_V222 === "1";

const R1 = "1a000000-0000-4000-8000-0000000000a1";
const R2 = "1a000000-0000-4000-8000-0000000000a2";
const S1 = "2b000000-0000-4000-8000-0000000000b1";
const S2 = "2b000000-0000-4000-8000-0000000000b2";
const UNKNOWN = "9f000000-0000-4000-8000-0000000000f9";
const SYSTEM = "3c000000-0000-4000-8000-0000000000c1";

const PG = {
  host: process.env.PG_HOST || "localhost",
  port: process.env.PG_PORT || "5432",
  user: process.env.PG_USER || "pguser",
  password: process.env.PG_PASSWORD || process.env.PG_PASS || "pgpass",
};

const pool = new Pool({
  host: PG.host,
  port: parseInt(PG.port, 10),
  user: PG.user,
  password: PG.password,
  database: DB_NAME,
  options: "-c search_path=nebula",
  max: 4,
});

// ── HTTP helper ───────────────────────────────────────────────────

function httpReq(method: string, pathname: string, body?: any): Promise<{ status: number; body: any }> {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : undefined;
    const url = new URL(pathname, BASE);
    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method,
      headers: bodyStr
        ? { "Content-Type": "application/json", "Content-Length": String(Buffer.byteLength(bodyStr)) }
        : {},
    };
    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode || 0, body: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode || 0, body: data });
        }
      });
    });
    req.on("error", reject);
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

const httpGet = (p: string) => httpReq("GET", p);
const httpPost = (p: string, body?: any) => httpReq("POST", p, body);

function assert(condition: boolean, msg: string): void {
  if (!condition) throw new Error(`FAIL: ${msg}`);
}

const jsonEq = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

// ── Database shape ────────────────────────────────────────────────

/**
 * V222 replaces `nebula.requirements` with a write-through projection over
 * `resolution.requirement`. The two shapes differ in where a fixture row has to
 * go, so the script asks the database rather than assuming.
 */
async function isPorted(): Promise<boolean> {
  const { rows } = await pool.query(
    `SELECT (SELECT count(*)::int FROM pg_trigger
              WHERE tgrelid = to_regclass('nebula.requirements')
                AND tgname = 'trg_requirements_compat_write'
                AND NOT tgisinternal) AS compat_trigger,
            COALESCE(to_regclass('resolution.requirement_port_ledger')::text, '') AS ledger`
  );
  return rows[0].compat_trigger === 1 && rows[0].ledger !== "";
}

async function blueprintCount(): Promise<number> {
  const { rows } = await pool.query("SELECT count(*)::int AS n FROM nebula.blueprints_history");
  return rows[0].n;
}

/**
 * Rows carrying a probe string — must stay 0 for a rejected request.
 *
 * The probe is sent as the request's `title` AND `goal`; only `goal` is folded
 * into the payload, so both columns are scanned (checked against a deliberate
 * write while developing this test: a title-only probe scanned in `payload`
 * does not detect anything).
 */
async function rowsCarrying(probe: string): Promise<number> {
  const { rows } = await pool.query(
    "SELECT count(*)::int AS n FROM nebula.blueprints_history WHERE title = $1 OR payload::text LIKE $2",
    [probe, `%${probe}%`]
  );
  return rows[0].n;
}

async function planNumbers(): Promise<string[]> {
  const { rows } = await pool.query(
    "SELECT plan_number FROM nebula.blueprints_history WHERE plan_number IS NOT NULL"
  );
  return rows.map((r: any) => r.plan_number);
}

// ── Fixture ───────────────────────────────────────────────────────
//
// Two requirements and two specifications, each specification governing a
// different requirement. Rows are written through `nebula.requirements` in both
// shapes: pre-port that is the auto-updatable view over the legacy history, and
// post-port the write-through projection, whose INSTEAD OF trigger creates the
// canonical row (with the same id) for us.
//
// `resolution.specification` carries an FK to `resolution.requirement`, so
// pre-port the canonical row for each requirement has to exist before the
// specification can point at it. It is copied column-for-column from the legacy
// row so the port's identity digests agree — the same state the port leaves
// behind, reached from the other side.

async function seed(ported: boolean): Promise<void> {
  await pool.query("DELETE FROM resolution.specification WHERE id = ANY($1::uuid[])", [[S1, S2]]);
  await pool.query(
    `DELETE FROM nebula.requirements WHERE id = ANY($1::uuid[])`,
    [[R1, R2]]
  );
  await pool.query("DELETE FROM nebula.requirements_history WHERE id = ANY($1::uuid[])", [[R1, R2]]);
  await pool.query("DELETE FROM resolution.requirement WHERE id = ANY($1::uuid[])", [[R1, R2]]);

  await pool.query(
    `INSERT INTO nebula.requirements (id, system_id, title)
     VALUES ($1, $3, 'Blueprint list fixture A'), ($2, $3, 'Blueprint list fixture B')`,
    [R1, R2, SYSTEM]
  );

  if (!ported) {
    const { rows } = await pool.query(
      // `work_request_dco` is a V222 carry-over column: it does not exist on
      // `resolution.requirement` before the port, and the fixture rows carry it
      // as NULL, so the copy omits it on both sides.
      `INSERT INTO resolution.requirement
         (id, source_type, system_id, subsystem_id, feature_id, title, description,
          status, priority, req_type, start_date, completion_date, acceptance_criteria,
          candidate_id, conduit_plan_id, asset_id,
          created_at, valid_from, valid_until, recorded_on_dt)
       SELECT id, CASE WHEN candidate_id IS NOT NULL THEN 'candidate' ELSE 'manual' END,
              system_id, subsystem_id, feature_id, title, description,
              status, priority, req_type, start_date, completion_date, acceptance_criteria,
              candidate_id, conduit_plan_id, asset_id,
              created_at, valid_from, valid_until, recorded_on_dt
         FROM nebula.requirements
        WHERE id = ANY($1::uuid[])
       ON CONFLICT (id) DO NOTHING
       RETURNING id::text`,
      [[R1, R2]]
    );
    assert(rows.length === 2, `expected 2 canonical fixture rows, got ${rows.length}`);
  }

  await pool.query(
    `INSERT INTO resolution.specification (id, requirement_id, agenda_id, revision_number, revision_type)
     VALUES ($1, $3, gen_random_uuid(), 1, 'created'), ($2, $4, gen_random_uuid(), 1, 'created')`,
    [S1, S2, R1, R2]
  );
}

async function cleanup(before: { plans: string[]; blueprints: number }): Promise<void> {
  // Blueprints first: post-port the delete guard refuses a requirement that a
  // live blueprint still names, and this script creates exactly such blueprints.
  const created = (await planNumbers()).filter((p) => !before.plans.includes(p));
  if (created.length > 0) {
    await pool.query("DELETE FROM nebula.blueprints_history WHERE plan_number = ANY($1::text[])", [
      created,
    ]);
  }
  await pool.query("DELETE FROM resolution.specification WHERE id = ANY($1::uuid[])", [[S1, S2]]);
  // Through the view so the pre-port and post-port delete paths each do their job.
  await pool.query("DELETE FROM nebula.requirements WHERE id = ANY($1::uuid[])", [[R1, R2]]);
  await pool.query("DELETE FROM nebula.requirements_history WHERE id = ANY($1::uuid[])", [[R1, R2]]);
  await pool.query("DELETE FROM resolution.requirement WHERE id = ANY($1::uuid[])", [[R1, R2]]);
  await pool.end();
}

/** Re-read a plan through both read endpoints and return its id fields. */
async function readBack(planNumber: string): Promise<{ single: any; listed: any }> {
  const single = await httpGet(`/api/plans/${encodeURIComponent(planNumber)}`);
  assert(single.status === 200, `GET /api/plans/${planNumber} expected 200, got ${single.status}`);
  assert(single.body.id === planNumber, `GET single id = ${JSON.stringify(single.body.id)}`);

  // Throwaway DBs hold a handful of rows: one page is enough. If the list
  // ever grows past the page size this lookup — not the endpoint — needs work.
  const list = await httpGet(`/api/plans?pageSize=100&page=1`);
  assert(list.status === 200, `GET /api/plans expected 200, got ${list.status}`);
  const listed = (list.body.items || []).find((item: any) => item.id === planNumber);
  assert(!!listed, `created plan ${planNumber} missing from GET /api/plans`);
  return { single: single.body, listed };
}

function applyV222(): void {
  const files = [
    "sql/V220__work_request_requirement_and_artifact_links.sql",
    "sql/V222__port_requirements_to_resolution.sql",
  ].flatMap((rel) => ["-f", path.join(REPO_ROOT, rel)]);

  const result = spawnSync(
    "psql",
    ["-w", "-q", "-v", "ON_ERROR_STOP=1", "-d", DB_NAME, ...files],
    {
      env: { ...process.env, PGHOST: PG.host, PGPORT: PG.port, PGUSER: PG.user, PGPASSWORD: PG.password },
      encoding: "utf8",
    }
  );
  if (result.status !== 0) {
    throw new Error(`applying V220+V222 failed (exit ${result.status}):\n${result.stderr}`);
  }
}

async function main(): Promise<void> {
  const before = { plans: await planNumbers(), blueprints: 0 };
  before.blueprints = await blueprintCount();

  try {
    const who = await pool.query("SELECT current_database() AS db");
    assert(who.rows[0].db === DB_NAME, `pool dialed ${who.rows[0].db}, expected ${DB_NAME}`);

    const health = await httpGet("/health");
    assert(health.status === 200 && health.body.db === true, `server not healthy: ${JSON.stringify(health.body)}`);

    const portedAtStart = await isPorted();
    if (APPLY_V222) {
      assert(!portedAtStart, "P4A_APPLY_V222=1 needs an unported database; this one already has V222");
    }
    console.log(`database ${DB_NAME}: ${portedAtStart ? "V222 applied" : "pre-port"}`);

    await seed(portedAtStart);

    // ── 1. both lists round-trip ─────────────────────────────────
    const create = await httpPost("/api/plans", {
      title: `Blueprint carrying requirement and specification sets ${DB_NAME}`,
      requirement_ids: [R1, R2],
      specification_ids: [S1],
    });
    assert(create.status === 201, `create expected 201, got ${create.status}: ${JSON.stringify(create.body)}`);
    assert(create.body.created === true, "create did not report created");
    assert(jsonEq(create.body.requirementIds, [R1, R2]), `create requirementIds = ${JSON.stringify(create.body.requirementIds)}`);
    assert(jsonEq(create.body.specificationIds, [S1]), `create specificationIds = ${JSON.stringify(create.body.specificationIds)}`);
    // The fields existing clients consume are untouched.
    assert(create.body.requirementRef === null, "singular requirementRef should stay null");
    assert(create.body.specRef === null, "singular specRef should stay null");

    const planNumber: string = create.body.planNumber;

    const payload = await pool.query(
      "SELECT payload FROM nebula.blueprints_history WHERE plan_number = $1",
      [planNumber]
    );
    assert(payload.rows.length === 1, "blueprint row was not written");
    assert(jsonEq(payload.rows[0].payload.requirement_ids, [R1, R2]), "payload.requirement_ids was not persisted");
    assert(jsonEq(payload.rows[0].payload.specification_ids, [S1]), "payload.specification_ids was not persisted");

    const preRead = await readBack(planNumber);
    assert(jsonEq(preRead.single.requirementIds, [R1, R2]), `GET single requirementIds = ${JSON.stringify(preRead.single.requirementIds)}`);
    assert(jsonEq(preRead.single.specificationIds, [S1]), `GET single specificationIds = ${JSON.stringify(preRead.single.specificationIds)}`);
    assert(jsonEq(preRead.listed.requirementIds, [R1, R2]), `GET list requirementIds = ${JSON.stringify(preRead.listed.requirementIds)}`);
    assert(jsonEq(preRead.listed.specificationIds, [S1]), `GET list specificationIds = ${JSON.stringify(preRead.listed.specificationIds)}`);

    // ── 2. rejections write nothing ──────────────────────────────
    // Row counts taken around each POST; the payload scan catches a write that
    // somehow balanced the count (or a partial write of the rejected id).
    // Each rejection carries a unique probe in title+goal. A written row is the
    // only way that string can reach the table, so a zero count is evidence of
    // "nothing stored" rather than an inference from the status code.
    const rejectTitle = (what: string) => `REJECTED ${what} ${DB_NAME}`;
    const rejected: Array<{ label: string; body: any; field: string; code: string; probe: string }> = [
      {
        label: "unknown requirement id",
        body: { title: rejectTitle("unknown requirement"), goal: rejectTitle("unknown requirement"), requirement_ids: [R1, UNKNOWN] },
        field: "requirement_ids[1]",
        code: "unknown_requirement_id",
        probe: rejectTitle("unknown requirement"),
      },
      {
        label: "specification outside the named requirement set",
        body: { title: rejectTitle("out-of-scope specification"), goal: rejectTitle("out-of-scope specification"), requirement_ids: [R1], specification_ids: [S2] },
        field: "specification_ids[0]",
        code: "specification_outside_blueprint",
        probe: rejectTitle("out-of-scope specification"),
      },
      {
        label: "specification id sent as a requirement",
        body: { title: rejectTitle("wrong entity type"), goal: rejectTitle("wrong entity type"), requirement_ids: [S1] },
        field: "requirement_ids[0]",
        code: "wrong_entity_type",
        probe: rejectTitle("wrong entity type"),
      },
      {
        label: "governed specification with no requirement (orphan list)",
        body: { title: rejectTitle("orphan specification"), goal: rejectTitle("orphan specification"), specification_ids: [S1] },
        field: "specification_ids[0]",
        code: "specification_outside_blueprint",
        probe: rejectTitle("orphan specification"),
      },
      {
        label: "governed specRef with no requirement (orphan primary)",
        body: { title: rejectTitle("orphan primary spec"), goal: rejectTitle("orphan primary spec"), specRef: S1 },
        field: "specRef",
        code: "specification_outside_blueprint",
        probe: rejectTitle("orphan primary spec"),
      },
      {
        label: "specRef left out of specification_ids",
        body: {
          title: rejectTitle("incoherent spec refs"),
          goal: rejectTitle("incoherent spec refs"),
          requirement_ids: [R1, R2],
          specification_ids: [S1],
          specRef: S2,
        },
        field: "specification_ids",
        code: "incoherent_blueprint_refs",
        probe: rejectTitle("incoherent spec refs"),
      },
    ];

    for (const testCase of rejected) {
      // One count on the table that stores blueprints; the probe below (not
      // this count) is what proves the rejected title/goal reached no row.
      const blueprintCountBefore = await blueprintCount();

      const response = await httpPost("/api/plans", testCase.body);
      assert(
        response.status === 400,
        `${testCase.label} expected 400, got ${response.status}: ${JSON.stringify(response.body)}`
      );
      assert(response.body.field === testCase.field, `${testCase.label} field = ${JSON.stringify(response.body.field)}`);
      assert(response.body.code === testCase.code, `${testCase.label} code = ${JSON.stringify(response.body.code)}`);
      assert(
        typeof response.body.error === "string" && response.body.error.includes(testCase.field),
        `${testCase.label} error should name ${testCase.field}: ${JSON.stringify(response.body.error)}`
      );

      const blueprintCountAfter = await blueprintCount();
      assert(
        blueprintCountAfter === blueprintCountBefore,
        `${testCase.label} changed the blueprint row count (${blueprintCountBefore} -> ${blueprintCountAfter})`
      );
      assert(
        (await rowsCarrying(testCase.probe)) === 0,
        `${testCase.label} left a blueprint row behind`
      );
    }

    // The rejected unknown id itself never reached any stored payload.
    assert(
      (await pool.query("SELECT count(*)::int AS n FROM nebula.blueprints_history WHERE payload::text LIKE $1", [`%${UNKNOWN}%`])).rows[0].n === 0,
      "a rejected id reached a stored blueprint payload"
    );

    // ── 3. the port moves the rows; the ids stay ─────────────────
    if (APPLY_V222) {
      assert((await blueprintCount()) === before.blueprints + 1, "expected exactly one blueprint before porting");

      applyV222();
      assert(await isPorted(), "V222 ran but the compatibility projection is not installed");

      // The export the endpoint validated against has been replaced under it.
      const postRead = await readBack(planNumber);
      assert(jsonEq(postRead.single.requirementIds, [R1, R2]), `post-port GET single requirementIds = ${JSON.stringify(postRead.single.requirementIds)}`);
      assert(jsonEq(postRead.single.specificationIds, [S1]), `post-port GET single specificationIds = ${JSON.stringify(postRead.single.specificationIds)}`);
      assert(jsonEq(postRead.listed.requirementIds, [R1, R2]), `post-port GET list requirementIds = ${JSON.stringify(postRead.listed.requirementIds)}`);
      assert(jsonEq(postRead.listed.specificationIds, [S1]), `post-port GET list specificationIds = ${JSON.stringify(postRead.listed.specificationIds)}`);

      // Identity: the ported rows carry the same ids, and the ledger proves it.
      const viewRows = await pool.query(
        "SELECT id::text AS id FROM nebula.requirements WHERE id = ANY($1::uuid[]) ORDER BY id",
        [[R1, R2]]
      );
      assert(viewRows.rows.length === 2, `post-port nebula.requirements resolves ${viewRows.rows.length} of 2 ids`);
      const canonicalRows = await pool.query(
        "SELECT id::text AS id FROM resolution.requirement WHERE id = ANY($1::uuid[]) ORDER BY id",
        [[R1, R2]]
      );
      assert(canonicalRows.rows.length === 2, `post-port resolution.requirement resolves ${canonicalRows.rows.length} of 2 ids`);
      assert(
        viewRows.rows.map((r: any) => r.id).join(",") === canonicalRows.rows.map((r: any) => r.id).join(","),
        "the projection and the canonical table disagree on the ids"
      );
      const ledger = await pool.query(
        `SELECT count(*)::int AS total,
                count(*) FILTER (WHERE source_id <> target_id)::int AS rekeyed
           FROM resolution.requirement_port_ledger
          WHERE source_id = ANY($1::uuid[])`,
        [[R1, R2]]
      );
      assert(ledger.rows[0].total === 2, `port ledger records ${ledger.rows[0].total} of 2 ids`);
      assert(ledger.rows[0].rekeyed === 0, "V222 re-keyed a requirement; stored blueprint ids would diverge");

      // A blueprint posted after the port validates and stores against it too.
      const afterPort = await httpPost("/api/plans", {
        title: `Blueprint created after the port ${DB_NAME}`,
        requirement_ids: [R2],
        specification_ids: [S2],
      });
      assert(
        afterPort.status === 201,
        `post-port create expected 201, got ${afterPort.status}: ${JSON.stringify(afterPort.body)}`
      );
      const afterPortRead = await readBack(afterPort.body.planNumber);
      assert(jsonEq(afterPortRead.single.requirementIds, [R2]), "post-port create did not round-trip requirement_ids");
      assert(jsonEq(afterPortRead.single.specificationIds, [S2]), "post-port create did not round-trip specification_ids");
    }

    // ── 4. singular-only blueprints keep their old shape ─────────
    const singular = await httpPost("/api/plans", {
      title: `Blueprint with only the primary pair ${DB_NAME}`,
      requirementRef: R1,
      specRef: S1,
    });
    assert(singular.status === 201, `singular create expected 201, got ${singular.status}: ${JSON.stringify(singular.body)}`);
    assert(singular.body.requirementRef === R1, "singular requirementRef changed shape");
    assert(singular.body.specRef === S1, "singular specRef changed shape");
    assert(jsonEq(singular.body.requirementIds, []), `absent lists should read as [] not ${JSON.stringify(singular.body.requirementIds)}`);

    console.log(
      `PASS: POST /api/plans carries requirement_ids / specification_ids and rejects bad refs ` +
        `(${portedAtStart ? "V222 applied" : APPLY_V222 ? "pre-port, ported mid-run" : "pre-port"})`
    );
  } finally {
    await cleanup(before);
  }
}

main().catch(async (err) => {
  console.error(err?.stack || err?.message || String(err));
  try {
    await pool.end();
  } catch {
    /* already closed */
  }
  process.exit(1);
});
