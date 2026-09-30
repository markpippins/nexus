/**
 * Execution telemetry emission for the harness walk boundary.
 *
 * Authority: architect **Decision 28** (`85c6d979`) and the A3 ruling set
 * (`a5991156`, `6629b009`, `dffa404e`).
 *
 * ## Why this is a separate module
 *
 * `harness.worker.ts` spawns child processes and talks to Redis/Postgres; testing the
 * emission path inside it means a full broker. The decision that matters — *what row gets
 * written, and when* — is pure, so it lives here where it can be exercised directly.
 *
 * ## The append-only discipline, in one sentence
 *
 * There is no "started" row and no "finished" row. The single INSERT is emitted **once**,
 * at a point where `doctrine_snapshot_id` and the census marker are both already final.
 * Two phase-rows would be the `sol_script.invoke_log` anti-pattern, which this table's
 * append-only trigger exists to make impossible.
 *
 * ## Never fatal
 *
 * Telemetry failing must never fail a walk. A missing census row is a gap in the record; a
 * crashed harness is a lost execution. Every call is therefore wrapped so the exception is
 * reported and swallowed, and a failure is recorded so it is visible rather than silent.
 */

import { readFileSync } from "fs";
import { prepareExecutionEmission, censusArgvFromEnv } from "./census-emit.ts";
import { resolveSessionId } from "./execution-identity.ts";
import { buildDoctrineSnapshot, type DoctrineSnapshot } from "./doctrine-snapshot.ts";

export interface ExecutionTelemetryInput {
  /** Raw run params, used to pick up an optional caller-supplied named session. */
  params: Record<string, any>;
  /** The prompt actually in force — `tackle.prompts.body_md` after substitution. */
  systemPrompt: string;
  /** The procedure-card index in force for the role, as loaded from Redis. */
  procedureIndex: unknown[];
  /** The ratified governing text (Decision 28 Ruling 3). */
  bootstrap: string;
  sourceNamespace: string;
  executorId: string;
  executedByRole: string;
  executedByModel: string;
  ticketId?: string | null;
  workItemId?: string | null;
  outcomeStatus: string;
  outcomeDetail?: string | null;
  reviewEvent?: boolean;
  /** Minted once at walk start so the sampling decision is reproducible and emit-once. */
  executionId?: string;
  createdAt?: Date;
}

export interface ExecutionTelemetryResult {
  ok: boolean;
  /** Null when the census was not enabled — the row is still written, with a disabled marker. */
  sampled: boolean | null;
  censusId: string | null;
  executionId: string;
  doctrineSnapshotId: string | null;
  errors: string[];
}

export type PgQuery = (text: string, values?: unknown[]) => Promise<unknown>;

/**
 * Resolve the ratified governing text.
 *
 * Decision 28 Ruling 3: bootstrap is the **frozen, reviewed** text that governs the walk,
 * explicitly not the mutable latest. Read from a named artifact so the frame is stable
 * across runs; a walk's frame identity must not move because an unrelated file was edited.
 */
export function loadGoverningText(path: string | undefined): string {
  if (!path) {
    throw new Error(
      "governing text path is not configured (NEXUS_GOVERNING_TEXT_PATH). Decision 28 " +
        "Ruling 3 requires bootstrap to be the ratified, frozen governing text, not the " +
        "mutable latest, so it must be a named artifact. Refusing to invent one.",
    );
  }
  const text = readFileSync(path, "utf8");
  if (text.trim().length === 0) {
    throw new Error(`governing text at ${path} is empty; an empty frame would be a fabrication`);
  }
  return text;
}

/**
 * Compose the one append-only row for a walk.
 *
 * Returns the statement and values rather than executing it, so the caller owns the
 * transaction and so the composition is testable without a database.
 */
export function composeExecutionRow(
  input: ExecutionTelemetryInput,
  env: Record<string, string | undefined>,
  extraArgs: readonly string[] = [],
): {
  executionId: string;
  sampled: boolean;
  censusId: string | null;
  doctrineSnapshot: DoctrineSnapshot;
  text: string;
  values: unknown[];
  errors: string[];
} {
  const errors: string[] = [];

  // Env synthesises argv; the single parser still owns every rule (A3 flag transport).
  const argv = [...extraArgs, ...censusArgvFromEnv(env).argv];
  // A caller-supplied named session wins; never mint a second identity when one exists.
  const sessionId = resolveSessionId({
    supplied: input.params?.session_id ?? null,
    source: input.sourceNamespace,
    executorId: input.executorId,
  });

  const doctrineSnapshot = buildDoctrineSnapshot({
    systemPrompt: input.systemPrompt,
    bootstrap: input.bootstrap,
    activeProcedureCards: input.procedureIndex,
  });

  const prepared = prepareExecutionEmission(
    argv,
    {
      sessionId,
      sourceNamespace: input.sourceNamespace,
      ticketId: input.ticketId ?? null,
      workItemId: input.workItemId ?? null,
      doctrineSnapshotId: doctrineSnapshot.snapshot_id,
      procedureCardSetHash: doctrineSnapshot.procedure_card_set_hash,
      outcomeStatus: input.outcomeStatus,
      outcomeDetail: input.outcomeDetail ?? null,
      executedByRole: input.executedByRole,
      executedByModel: input.executedByModel,
    },
    { reviewEvent: Boolean(input.reviewEvent), executionId: input.executionId },
  );
  if (!prepared.ok) errors.push(...prepared.errors);

  return {
    executionId: prepared.envelope?.execution.execution_id ?? (input.executionId ?? ""),
    sampled: prepared.envelope?.census.sampled ?? false,
    censusId: prepared.envelope?.execution.census_id ?? null,
    doctrineSnapshot,
    text: prepared.insert?.text ?? "",
    values: prepared.insert?.values ?? [],
    errors,
  };
}

/**
 * Emit the row. Never throws.
 *
 * A census that cannot record itself is a defect worth surfacing, but it is not worth failing
 * an execution over — so failures are returned and logged, never propagated.
 */
export async function emitExecutionTelemetry(
  input: ExecutionTelemetryInput,
  query: PgQuery,
  env: Record<string, string | undefined> = process.env,
  extraArgs: readonly string[] = [],
  onError?: (message: string) => void,
): Promise<ExecutionTelemetryResult> {
  const errors: string[] = [];
  let executionId = input.executionId ?? "";
  try {
    const row = composeExecutionRow(input, env, extraArgs);
    executionId = row.executionId;
    if (row.errors.length) {
      errors.push(...row.errors);
      // Do not write a row that failed validation: the table is append-only, so a bad row
      // could never be corrected afterwards.
      throw new Error(row.errors.join("; "));
    }
    await query(row.text, row.values);
    return {
      ok: true,
      sampled: row.sampled,
      censusId: row.censusId,
      executionId: row.executionId,
      doctrineSnapshotId: row.doctrineSnapshot.snapshot_id,
      errors,
    };
  } catch (error: any) {
    const message = `execution telemetry not written: ${error?.message ?? error}`;
    errors.push(message);
    onError?.(message);
    return {
      ok: false,
      sampled: null,
      censusId: null,
      executionId,
      doctrineSnapshotId: null,
      errors,
    };
  }
}
