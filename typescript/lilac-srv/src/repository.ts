/**
 * Canonical Resolution persistence for the Lilac surface.
 *
 * Design rules this file must keep:
 *
 *   1. Every statement names a table in CANONICAL_TABLES, schema-qualified
 *      `resolution.`. A test parses this file and fails on anything else, so a
 *      future edit cannot quietly reintroduce a legacy-table write (AC4).
 *   2. Idempotency is enforced by the DATABASE (the unique indexes that
 *      predate this service), not by an in-process set: a replay arriving on a
 *      second replica, or after a restart, must classify the same way.
 *   3. A replay that reuses an identity with a different payload is refused
 *      with BOTH fingerprints; it is never merged and never overwritten.
 *   4. The envelope is recorded in resolution.mutation_provenance keyed by
 *      idempotency_key, so the same key cannot be spent on a different
 *      mutation later.
 */
import {
  classifyReplay,
  conflictRefusal,
  ContractRefusal,
  fingerprint,
  type Envelope,
  type OutcomeClass,
  type Surface,
} from "./contract";

export interface QueryResultLike {
  rows: any[];
}

/** The narrow database seam. Tests supply an in-memory implementation; the
 *  service supplies a pg Pool. Nothing else is reachable from here, which is
 *  what keeps the SQL auditable. */
export interface Queryable {
  query(sql: string, params?: unknown[]): Promise<QueryResultLike>;
}

export interface Db extends Queryable {
  transaction<T>(fn: (tx: Queryable) => Promise<T>): Promise<T>;
}

export interface MutationResult {
  outcome: OutcomeClass;
  target_id: string;
  surface: Surface;
}

const PROVENANCE_INSERT = `
  INSERT INTO resolution.mutation_provenance
    (surface, target_id, source_implementation, runtime_route,
     source_identity, idempotency_key, contract_version)
  VALUES ($1,$2,$3,$4,$5,$6,$7)
  ON CONFLICT (idempotency_key) DO NOTHING
  RETURNING id
`;

const PROVENANCE_LOOKUP = `
  SELECT id, target_id, surface
    FROM resolution.mutation_provenance
   WHERE idempotency_key = $1
`;

const PROVENANCE_ATTACH = `
  UPDATE resolution.mutation_provenance
     SET target_id = COALESCE(target_id, $2)
   WHERE idempotency_key = $1
`;

export interface ReceiptInput {
  envelope: Envelope;
  producer_id: string;
  kind: string;
  source_system: string;
  source_receipt_id: string;
  payload: Record<string, unknown>;
  refs?: Record<string, unknown>;
}

export interface TicketInput {
  envelope: Envelope;
  workflow_ref: string;
  role: string;
  position: number;
  generation?: number;
  objective?: string;
  predecessor_receipt_id?: string | null;
}

export interface TransitionInput {
  envelope: Envelope;
  ticket_id: string;
  input_receipt_id: string;
  from_status: string;
  to_status: string;
  fanout_policy_version?: number;
  outcome_class?: string;
  payload?: Record<string, unknown>;
}

export interface FanoutInput {
  envelope: Envelope;
  input_receipt_id: string;
  kind: string;
  fan_out_policy_version?: number;
  produced: Record<string, unknown>[];
  outcome?: string;
}

export class LilacRepository {
  constructor(private readonly db: Db) {}

  // ── shared internals ────────────────────────────────────────────────

  /** Record the envelope, or discover that this key was already spent. */
  private async claimProvenance(
    tx: Queryable,
    surface: Surface,
    envelope: Envelope,
  ): Promise<{ inserted: boolean; target_id: string | null }> {
    const ins = await tx.query(PROVENANCE_INSERT, [
      surface,
      null,
      envelope.source_implementation,
      envelope.runtime_route,
      envelope.source_identity,
      envelope.idempotency_key,
      envelope.contract_version,
    ]);
    if (ins.rows.length > 0) return { inserted: true, target_id: null };

    const prior = await tx.query(PROVENANCE_LOOKUP, [envelope.idempotency_key]);
    const row = prior.rows[0];
    if (!row) {
      throw new ContractRefusal(
        "PROVENANCE_ORPHAN",
        409,
        "mutation refused: idempotency key is taken but its provenance row is unreadable",
        { surface, idempotency_key: envelope.idempotency_key },
      );
    }
    if (row.surface !== surface) {
      throw new ContractRefusal(
        "IDEMPOTENCY_KEY_REUSED",
        409,
        `mutation refused: idempotency key was already spent on ${row.surface}, not ${surface}`,
        {
          idempotency_key: envelope.idempotency_key,
          recorded_surface: row.surface,
          incoming_surface: surface,
          target_id: row.target_id,
        },
      );
    }
    return { inserted: false, target_id: row.target_id };
  }

  /** Claim the key for a NEW entity. A key already spent on this surface
   *  without a matching identity is refused: otherwise one key could mint two
   *  entities and the "unique per mutation" contract would be fiction. */
  private async claimForNewEntity(
    tx: Queryable,
    surface: Surface,
    envelope: Envelope,
  ): Promise<void> {
    const claim = await this.claimProvenance(tx, surface, envelope);
    if (!claim.inserted) {
      throw new ContractRefusal(
        "PROVENANCE_ORPHAN",
        409,
        `mutation refused: idempotency key already spent on ${surface} but no ${surface} exists for this identity`,
        {
          surface,
          idempotency_key: envelope.idempotency_key,
          recorded_target_id: claim.target_id,
        },
      );
    }
  }

  private async attachProvenance(
    tx: Queryable,
    envelope: Envelope,
    targetId: string,
  ): Promise<void> {
    await tx.query(PROVENANCE_ATTACH, [envelope.idempotency_key, targetId]);
  }

  // ── receipt issuance ────────────────────────────────────────────────

  async issueReceipt(input: ReceiptInput): Promise<MutationResult> {
    const fp = fingerprint(input.payload);
    return this.db.transaction(async (tx) => {
      const existing = await tx.query(
        `SELECT id, payload_fingerprint
           FROM resolution.receipt
          WHERE source_system = $1 AND source_receipt_id = $2`,
        [input.source_system, input.source_receipt_id],
      );
      if (existing.rows.length > 0) {
        const row = existing.rows[0];
        const outcome = classifyReplay("receipt", row.payload_fingerprint, fp, {
          source_system: input.source_system,
          source_receipt_id: input.source_receipt_id,
        });
        const claim = await this.claimProvenance(tx, "receipt", input.envelope);
        await this.attachProvenance(tx, input.envelope, String(row.id));
        void claim;
        return { outcome, target_id: String(row.id), surface: "receipt" as Surface };
      }

      await this.claimForNewEntity(tx, "receipt", input.envelope);
      const ins = await tx.query(
        `INSERT INTO resolution.receipt
           (producer_id, kind, source_system, source_receipt_id,
            payload_fingerprint, payload, refs, contract_version,
            source_implementation, runtime_route, source_identity, idempotency_key)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
         ON CONFLICT (source_system, source_receipt_id) DO NOTHING
         RETURNING id`,
        [
          input.producer_id,
          input.kind,
          input.source_system,
          input.source_receipt_id,
          fp,
          JSON.stringify(input.payload),
          JSON.stringify(input.refs ?? {}),
          input.envelope.contract_version,
          input.envelope.source_implementation,
          input.envelope.runtime_route,
          input.envelope.source_identity,
          input.envelope.idempotency_key,
        ],
      );
      if (ins.rows.length === 0) {
        // Raced with another writer between SELECT and INSERT; re-read and
        // classify against the winner rather than inventing an outcome.
        const again = await tx.query(
          `SELECT id, payload_fingerprint FROM resolution.receipt
            WHERE source_system = $1 AND source_receipt_id = $2`,
          [input.source_system, input.source_receipt_id],
        );
        if (again.rows.length === 0) {
          throw conflictRefusal("receipt", "(none)", fp, { raced: true });
        }
        const row = again.rows[0];
        return {
          outcome: classifyReplay("receipt", row.payload_fingerprint, fp),
          target_id: String(row.id),
          surface: "receipt" as Surface,
        };
      }
      const id = String(ins.rows[0].id);
      await this.attachProvenance(tx, input.envelope, id);
      return { outcome: "accepted" as OutcomeClass, target_id: id, surface: "receipt" as Surface };
    });
  }

  // ── ticket lifecycle ────────────────────────────────────────────────

  async issueTicket(input: TicketInput): Promise<MutationResult> {
    const generation = input.generation ?? 0;
    const identity = {
      workflow_ref: input.workflow_ref,
      role: input.role,
      position: input.position,
      generation,
      objective: input.objective ?? "",
    };
    const fp = fingerprint(identity);
    return this.db.transaction(async (tx) => {
      const existing = await tx.query(
        `SELECT id, objective FROM resolution.ticket
          WHERE workflow_ref = $1 AND role = $2 AND position = $3 AND generation = $4`,
        [input.workflow_ref, input.role, input.position, generation],
      );
      if (existing.rows.length > 0) {
        const row = existing.rows[0];
        const storedFp = fingerprint({ ...identity, objective: String(row.objective ?? "") });
        const outcome = classifyReplay("ticket", storedFp, fp, {
          workflow_ref: input.workflow_ref,
          role: input.role,
          position: input.position,
          generation,
        });
        await this.claimProvenance(tx, "ticket", input.envelope);
        await this.attachProvenance(tx, input.envelope, String(row.id));
        return { outcome, target_id: String(row.id), surface: "ticket" as Surface };
      }

      await this.claimForNewEntity(tx, "ticket", input.envelope);
      const ins = await tx.query(
        `INSERT INTO resolution.ticket
           (workflow_ref, role, position, status, predecessor_receipt_id,
            generation, objective, contract_version,
            source_implementation, runtime_route, source_identity, idempotency_key)
         VALUES ($1,$2,$3,'pending',$4,$5,$6,$7,$8,$9,$10,$11)
         ON CONFLICT (workflow_ref, role, position, generation) DO NOTHING
         RETURNING id`,
        [
          input.workflow_ref,
          input.role,
          input.position,
          input.predecessor_receipt_id ?? null,
          generation,
          identity.objective,
          input.envelope.contract_version,
          input.envelope.source_implementation,
          input.envelope.runtime_route,
          input.envelope.source_identity,
          input.envelope.idempotency_key,
        ],
      );
      if (ins.rows.length === 0) {
        const again = await tx.query(
          `SELECT id, objective FROM resolution.ticket
            WHERE workflow_ref = $1 AND role = $2 AND position = $3 AND generation = $4`,
          [input.workflow_ref, input.role, input.position, generation],
        );
        if (again.rows.length === 0) {
          throw conflictRefusal("ticket", "(none)", fp, { raced: true });
        }
        const row = again.rows[0];
        const storedFp = fingerprint({ ...identity, objective: String(row.objective ?? "") });
        return {
          outcome: classifyReplay("ticket", storedFp, fp),
          target_id: String(row.id),
          surface: "ticket" as Surface,
        };
      }
      const id = String(ins.rows[0].id);
      await this.attachProvenance(tx, input.envelope, id);
      return { outcome: "accepted" as OutcomeClass, target_id: id, surface: "ticket" as Surface };
    });
  }

  // ── transitions ─────────────────────────────────────────────────────

  async recordTransition(input: TransitionInput): Promise<MutationResult> {
    const policy = input.fanout_policy_version ?? 1;
    const payload = input.payload ?? {};
    const fp = fingerprint({
      ticket_id: input.ticket_id,
      from_status: input.from_status,
      to_status: input.to_status,
      input_receipt_id: input.input_receipt_id,
      fanout_policy_version: policy,
      payload,
    });
    return this.db.transaction(async (tx) => {
      const existing = await tx.query(
        `SELECT id, payload FROM resolution.ticket_transition
          WHERE ticket_id = $1 AND from_status = $2 AND to_status = $3
            AND input_receipt_id = $4 AND fanout_policy_version = $5`,
        [input.ticket_id, input.from_status, input.to_status, input.input_receipt_id, policy],
      );
      if (existing.rows.length > 0) {
        const row = existing.rows[0];
        const storedFp = fingerprint({
          ticket_id: input.ticket_id,
          from_status: input.from_status,
          to_status: input.to_status,
          input_receipt_id: input.input_receipt_id,
          fanout_policy_version: policy,
          payload: row.payload ?? {},
        });
        const outcome = classifyReplay("ticket_transition", storedFp, fp, {
          ticket_id: input.ticket_id,
          from_status: input.from_status,
          to_status: input.to_status,
        });
        await this.claimProvenance(tx, "ticket_transition", input.envelope);
        await this.attachProvenance(tx, input.envelope, String(row.id));
        return {
          outcome,
          target_id: String(row.id),
          surface: "ticket_transition" as Surface,
        };
      }

      await this.claimForNewEntity(tx, "ticket_transition", input.envelope);
      const ins = await tx.query(
        `INSERT INTO resolution.ticket_transition
           (ticket_id, input_receipt_id, from_status, to_status,
            fanout_policy_version, outcome_class, payload,
            source_implementation, runtime_route, source_identity, idempotency_key)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
         ON CONFLICT (ticket_id, from_status, to_status, input_receipt_id, fanout_policy_version)
           DO NOTHING
         RETURNING id`,
        [
          input.ticket_id,
          input.input_receipt_id,
          input.from_status,
          input.to_status,
          policy,
          input.outcome_class ?? input.to_status,
          JSON.stringify(payload),
          input.envelope.source_implementation,
          input.envelope.runtime_route,
          input.envelope.source_identity,
          input.envelope.idempotency_key,
        ],
      );
      if (ins.rows.length === 0) {
        const again = await tx.query(
          `SELECT id, payload FROM resolution.ticket_transition
            WHERE ticket_id = $1 AND from_status = $2 AND to_status = $3
              AND input_receipt_id = $4 AND fanout_policy_version = $5`,
          [input.ticket_id, input.from_status, input.to_status, input.input_receipt_id, policy],
        );
        if (again.rows.length === 0) {
          throw conflictRefusal("ticket_transition", "(none)", fp, { raced: true });
        }
        const row = again.rows[0];
        const storedFp = fingerprint({
          ticket_id: input.ticket_id,
          from_status: input.from_status,
          to_status: input.to_status,
          input_receipt_id: input.input_receipt_id,
          fanout_policy_version: policy,
          payload: row.payload ?? {},
        });
        return {
          outcome: classifyReplay("ticket_transition", storedFp, fp),
          target_id: String(row.id),
          surface: "ticket_transition" as Surface,
        };
      }
      const id = String(ins.rows[0].id);
      await this.attachProvenance(tx, input.envelope, id);
      return {
        outcome: "accepted" as OutcomeClass,
        target_id: id,
        surface: "ticket_transition" as Surface,
      };
    });
  }

  // ── fan-out ─────────────────────────────────────────────────────────

  async recordFanout(input: FanoutInput): Promise<MutationResult> {
    const policy = input.fan_out_policy_version ?? 1;
    const fp = fingerprint({
      input_receipt_id: input.input_receipt_id,
      kind: input.kind,
      fan_out_policy_version: policy,
      produced: input.produced,
    });
    // A fan-out conflict has a durable home on the ledger row, so the ledger
    // write must COMMIT before the refusal is raised. Deferring the throw is
    // what makes the record survive instead of rolling back with the refusal.
    let pendingConflict: { existingFingerprint: string; incomingFingerprint: string } | null = null;
    const result = await this.db.transaction(async (tx) => {
      const existing = await tx.query(
        `SELECT id, produced, conflict_fingerprints
           FROM resolution.fanout_transition
          WHERE input_receipt_id = $1 AND kind = $2 AND fan_out_policy_version = $3`,
        [input.input_receipt_id, input.kind, policy],
      );
      if (existing.rows.length > 0) {
        const row = existing.rows[0];
        const storedFp = fingerprint({
          input_receipt_id: input.input_receipt_id,
          kind: input.kind,
          fan_out_policy_version: policy,
          produced: row.produced ?? [],
        });
        if (storedFp !== fp) {
          await tx.query(
            `UPDATE resolution.fanout_transition
                SET conflict_fingerprints = $2
              WHERE id = $1`,
            [row.id, JSON.stringify({ existing: storedFp, incoming: fp })],
          );
          pendingConflict = { existingFingerprint: storedFp, incomingFingerprint: fp };
          return {
            outcome: "conflict" as OutcomeClass,
            target_id: String(row.id),
            surface: "fanout_transition" as Surface,
          };
        }
        const outcome = classifyReplay("fanout_transition", storedFp, fp, {
          input_receipt_id: input.input_receipt_id,
          kind: input.kind,
          fan_out_policy_version: policy,
        });
        await this.claimProvenance(tx, "fanout_transition", input.envelope);
        await this.attachProvenance(tx, input.envelope, String(row.id));
        return {
          outcome,
          target_id: String(row.id),
          surface: "fanout_transition" as Surface,
        };
      }

      await this.claimForNewEntity(tx, "fanout_transition", input.envelope);
      const ins = await tx.query(
        `INSERT INTO resolution.fanout_transition
           (input_receipt_id, kind, fan_out_policy_version, outcome, produced,
            source_implementation, runtime_route, source_identity, idempotency_key)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
         ON CONFLICT (input_receipt_id, kind, fan_out_policy_version) DO NOTHING
         RETURNING id`,
        [
          input.input_receipt_id,
          input.kind,
          policy,
          input.outcome ?? (input.produced.length > 0 ? "spawned" : "no-op"),
          JSON.stringify(input.produced),
          input.envelope.source_implementation,
          input.envelope.runtime_route,
          input.envelope.source_identity,
          input.envelope.idempotency_key,
        ],
      );
      if (ins.rows.length === 0) {
        const again = await tx.query(
          `SELECT id, produced FROM resolution.fanout_transition
            WHERE input_receipt_id = $1 AND kind = $2 AND fan_out_policy_version = $3`,
          [input.input_receipt_id, input.kind, policy],
        );
        if (again.rows.length === 0) {
          throw conflictRefusal("fanout_transition", "(none)", fp, { raced: true });
        }
        const row = again.rows[0];
        const storedFp = fingerprint({
          input_receipt_id: input.input_receipt_id,
          kind: input.kind,
          fan_out_policy_version: policy,
          produced: row.produced ?? [],
        });
        return {
          outcome: classifyReplay("fanout_transition", storedFp, fp),
          target_id: String(row.id),
          surface: "fanout_transition" as Surface,
        };
      }
      const id = String(ins.rows[0].id);
      await this.attachProvenance(tx, input.envelope, id);
      return {
        outcome: "accepted" as OutcomeClass,
        target_id: id,
        surface: "fanout_transition" as Surface,
      };
    });

    if (pendingConflict) {
      const conflict: { existingFingerprint: string; incomingFingerprint: string } =
        pendingConflict;
      throw conflictRefusal(
        "fanout_transition",
        conflict.existingFingerprint,
        conflict.incomingFingerprint,
        {
          input_receipt_id: input.input_receipt_id,
          kind: input.kind,
          fan_out_policy_version: policy,
        },
      );
    }
    return result;
  }

  // ── reads ───────────────────────────────────────────────────────────

  /** Ticket timeline: the ticket row plus its transitions in order, each with
   *  the receipt that drove it. Read-only, so no envelope is required. */
  async ticketTimeline(ticketId: string): Promise<Record<string, unknown>> {
    const ticket = await this.db.query(
      `SELECT id, workflow_ref, role, position, status, generation, objective,
              predecessor_receipt_id, contract_version, created_at, updated_at
         FROM resolution.ticket WHERE id = $1`,
      [ticketId],
    );
    if (ticket.rows.length === 0) {
      throw new ContractRefusal("PROVENANCE_ORPHAN", 404, "ticket not found", { ticket_id: ticketId });
    }
    const transitions = await this.db.query(
      `SELECT t.id, t.from_status, t.to_status, t.outcome_class,
              t.input_receipt_id, t.fanout_policy_version, t.created_at,
              r.kind AS receipt_kind, r.source_system, r.source_receipt_id
         FROM resolution.ticket_transition t
         LEFT JOIN resolution.receipt r ON r.id = t.input_receipt_id
        WHERE t.ticket_id = $1
        ORDER BY t.created_at ASC, t.id ASC`,
      [ticketId],
    );
    return { ticket: ticket.rows[0], transitions: transitions.rows };
  }

  /** Parity diagnostics: the recorded dual-read measurements, plus how many
   *  provenance rows exist per surface. Nothing is inferred — an empty
   *  evidence set reports `recorded: false` rather than a pass. */
  async parityDiagnostics(limit = 5): Promise<Record<string, unknown>> {
    const evidence = await this.db.query(
      `SELECT wr_id, window_start, window_end, matched, mismatched,
              unreadable, outcome, detail, recorded_at
         FROM resolution.parity_evidence
        ORDER BY window_end DESC, recorded_at DESC
        LIMIT $1`,
      [Math.max(1, Math.min(limit, 50))],
    );
    const surfaces = await this.db.query(
      `SELECT surface, count(*)::int AS mutations
         FROM resolution.mutation_provenance
        GROUP BY surface
        ORDER BY surface`,
    );
    return {
      recorded: evidence.rows.length > 0,
      evidence: evidence.rows,
      provenance: surfaces.rows,
    };
  }
}
