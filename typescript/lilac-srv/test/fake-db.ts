/**
 * In-memory stand-in for the resolution.* tables the repository talks to.
 *
 * It exists so the hermetic suite can prove THREE things the service claims
 * and a mock of our own methods could not prove:
 *
 *   - uniqueness is the classifier (inserts honour the natural keys),
 *   - a refusal writes nothing (transaction() snapshots and restores),
 *   - the SQL actually names the natural keys the real schema indexes.
 *
 * It parses the statements the repository issues rather than mirroring its
 * methods: if the SQL changes shape, these tests fail instead of silently
 * agreeing with a stub.
 */
import { randomUUID } from "crypto";
import type { Db, Queryable, QueryResultLike } from "../src/repository";

export interface ProvenanceRow {
  id: string;
  surface: string;
  target_id: string | null;
  source_implementation: string;
  runtime_route: string;
  source_identity: string;
  idempotency_key: string;
  contract_version: number;
}

export interface Tables {
  receipt: Record<string, any>[];
  ticket: Record<string, any>[];
  ticket_transition: Record<string, any>[];
  fanout_transition: Record<string, any>[];
  mutation_provenance: ProvenanceRow[];
  parity_evidence: Record<string, any>[];
}

const norm = (sql: string): string => sql.replace(/\s+/g, " ").trim();

const eq = (a: unknown, b: unknown): boolean => String(a) === String(b);

export class FakeDb implements Db, Queryable {
  tables: Tables;
  /** Every statement received, in order — used to assert "no SQL ran". */
  queries: string[] = [];

  constructor(seed?: Partial<Tables>) {
    this.tables = {
      receipt: [],
      ticket: [],
      ticket_transition: [],
      fanout_transition: [],
      mutation_provenance: [],
      parity_evidence: [],
      ...seed,
    };
  }

  private snapshot(): Tables {
    return JSON.parse(JSON.stringify(this.tables)) as Tables;
  }

  async transaction<T>(fn: (tx: Queryable) => Promise<T>): Promise<T> {
    const before = this.snapshot();
    try {
      return await fn(this);
    } catch (err) {
      this.tables = before;
      throw err;
    }
  }

  async query(sql: string, params: unknown[] = []): Promise<QueryResultLike> {
    return this.exec(sql, params);
  }

  private exec(sql: string, params: unknown[]): QueryResultLike {
    const s = norm(sql);
    this.queries.push(s);
    const t = this.tables;

    // ── provenance ────────────────────────────────────────────────────
    if (s.startsWith("INSERT INTO resolution.mutation_provenance")) {
      const [surface, target_id, impl, route, identity, key, version] = params;
      if (t.mutation_provenance.some((r) => r.idempotency_key === key)) return { rows: [] };
      const row: ProvenanceRow = {
        id: randomUUID(),
        surface: String(surface),
        target_id: target_id ? String(target_id) : null,
        source_implementation: String(impl),
        runtime_route: String(route),
        source_identity: String(identity),
        idempotency_key: String(key),
        contract_version: Number(version),
      };
      t.mutation_provenance.push(row);
      return { rows: [{ id: row.id }] };
    }
    if (s.startsWith("SELECT id, target_id, surface FROM resolution.mutation_provenance")) {
      const key = params[0];
      return {
        rows: t.mutation_provenance
          .filter((r) => r.idempotency_key === key)
          .map((r) => ({ id: r.id, target_id: r.target_id, surface: r.surface })),
      };
    }
    if (s.startsWith("UPDATE resolution.mutation_provenance")) {
      const [key, target] = params;
      const row = t.mutation_provenance.find((r) => r.idempotency_key === key);
      if (row && row.target_id === null) row.target_id = String(target);
      return { rows: [] };
    }

    // ── receipt ───────────────────────────────────────────────────────
    if (s.startsWith("SELECT id, payload_fingerprint FROM resolution.receipt")) {
      const [sourceSystem, sourceReceiptId] = params;
      return {
        rows: t.receipt
          .filter((r) => r.source_system === sourceSystem && r.source_receipt_id === sourceReceiptId)
          .map((r) => ({ id: r.id, payload_fingerprint: r.payload_fingerprint })),
      };
    }
    if (s.startsWith("INSERT INTO resolution.receipt")) {
      const [
        producer_id, kind, source_system, source_receipt_id,
        payload_fingerprint, payload, refs, contract_version,
        source_implementation, runtime_route, source_identity, idempotency_key,
      ] = params;
      const clash = t.receipt.some(
        (r) => r.source_system === source_system && r.source_receipt_id === source_receipt_id,
      );
      if (clash) return { rows: [] };
      const row = {
        id: randomUUID(), producer_id, kind, source_system, source_receipt_id,
        payload_fingerprint, payload: JSON.parse(String(payload)), refs: JSON.parse(String(refs)),
        contract_version, source_implementation, runtime_route, source_identity,
        idempotency_key, created_at: new Date().toISOString(),
      };
      t.receipt.push(row);
      return { rows: [{ id: row.id }] };
    }

    // ── ticket ────────────────────────────────────────────────────────
    if (s.startsWith("SELECT id, objective FROM resolution.ticket")) {
      const [workflow_ref, role, position, generation] = params;
      return {
        rows: t.ticket
          .filter(
            (r) =>
              r.workflow_ref === workflow_ref &&
              r.role === role &&
              eq(r.position, position) &&
              eq(r.generation, generation),
          )
          .map((r) => ({ id: r.id, objective: r.objective })),
      };
    }
    // NB: `ticket (` with the open paren — `ticket_transition` shares the
    // prefix and must not be routed to the ticket branch.
    if (s.startsWith("INSERT INTO resolution.ticket (")) {
      const [
        workflow_ref, role, position, predecessor, generation, objective,
        contract_version, source_implementation, runtime_route, source_identity, idempotency_key,
      ] = params;
      const clash = t.ticket.some(
        (r) =>
          r.workflow_ref === workflow_ref &&
          r.role === role &&
          eq(r.position, position) &&
          eq(r.generation, generation),
      );
      if (clash) return { rows: [] };
      const row = {
        id: randomUUID(), workflow_ref, role, position: Number(position), status: "pending",
        predecessor_receipt_id: predecessor, generation: Number(generation), objective,
        contract_version, source_implementation, runtime_route, source_identity, idempotency_key,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      t.ticket.push(row);
      return { rows: [{ id: row.id }] };
    }
    if (s.startsWith("SELECT id, workflow_ref, role, position, status")) {
      const id = params[0];
      return { rows: t.ticket.filter((r) => r.id === id) };
    }

    // ── ticket_transition ─────────────────────────────────────────────
    if (s.startsWith("SELECT id, payload FROM resolution.ticket_transition")) {
      const [ticket_id, from_status, to_status, input_receipt_id, policy] = params;
      return {
        rows: t.ticket_transition
          .filter(
            (r) =>
              r.ticket_id === ticket_id &&
              r.from_status === from_status &&
              r.to_status === to_status &&
              r.input_receipt_id === input_receipt_id &&
              eq(r.fanout_policy_version, policy),
          )
          .map((r) => ({ id: r.id, payload: r.payload })),
      };
    }
    if (s.startsWith("INSERT INTO resolution.ticket_transition")) {
      const [
        ticket_id, input_receipt_id, from_status, to_status, policy, outcome_class,
        payload, source_implementation, runtime_route, source_identity, idempotency_key,
      ] = params;
      const clash = t.ticket_transition.some(
        (r) =>
          r.ticket_id === ticket_id &&
          r.from_status === from_status &&
          r.to_status === to_status &&
          r.input_receipt_id === input_receipt_id &&
          eq(r.fanout_policy_version, policy),
      );
      if (clash) return { rows: [] };
      const row = {
        id: randomUUID(), ticket_id, input_receipt_id, from_status, to_status,
        fanout_policy_version: Number(policy), outcome_class, payload: JSON.parse(String(payload)),
        source_implementation, runtime_route, source_identity, idempotency_key,
        created_at: new Date().toISOString(),
      };
      t.ticket_transition.push(row);
      return { rows: [{ id: row.id }] };
    }
    if (s.startsWith("SELECT t.id, t.from_status")) {
      const ticketId = params[0];
      return {
        rows: t.ticket_transition
          .filter((r) => r.ticket_id === ticketId)
          .map((r) => ({
            id: r.id,
            from_status: r.from_status,
            to_status: r.to_status,
            outcome_class: r.outcome_class,
            input_receipt_id: r.input_receipt_id,
            fanout_policy_version: r.fanout_policy_version,
            created_at: r.created_at,
            receipt_kind: t.receipt.find((x) => x.id === r.input_receipt_id)?.kind ?? null,
            source_system: t.receipt.find((x) => x.id === r.input_receipt_id)?.source_system ?? null,
            source_receipt_id:
              t.receipt.find((x) => x.id === r.input_receipt_id)?.source_receipt_id ?? null,
          })),
      };
    }

    // ── fanout_transition ─────────────────────────────────────────────
    if (s.startsWith("SELECT id, produced, conflict_fingerprints FROM resolution.fanout_transition")) {
      const [input_receipt_id, kind, policy] = params;
      return {
        rows: t.fanout_transition
          .filter(
            (r) =>
              r.input_receipt_id === input_receipt_id &&
              r.kind === kind &&
              eq(r.fan_out_policy_version, policy),
          )
          .map((r) => ({
            id: r.id,
            produced: r.produced,
            conflict_fingerprints: r.conflict_fingerprints ?? null,
          })),
      };
    }
    if (s.startsWith("SELECT id, produced FROM resolution.fanout_transition")) {
      const [input_receipt_id, kind, policy] = params;
      return {
        rows: t.fanout_transition
          .filter(
            (r) =>
              r.input_receipt_id === input_receipt_id &&
              r.kind === kind &&
              eq(r.fan_out_policy_version, policy),
          )
          .map((r) => ({ id: r.id, produced: r.produced })),
      };
    }
    if (s.startsWith("UPDATE resolution.fanout_transition SET conflict_fingerprints")) {
      const [id, fps] = params;
      const row = t.fanout_transition.find((r) => r.id === id);
      if (row) row.conflict_fingerprints = JSON.parse(String(fps));
      return { rows: [] };
    }
    if (s.startsWith("INSERT INTO resolution.fanout_transition")) {
      const [
        input_receipt_id, kind, policy, outcome, produced,
        source_implementation, runtime_route, source_identity, idempotency_key,
      ] = params;
      const clash = t.fanout_transition.some(
        (r) =>
          r.input_receipt_id === input_receipt_id &&
          r.kind === kind &&
          eq(r.fan_out_policy_version, policy),
      );
      if (clash) return { rows: [] };
      const row = {
        id: randomUUID(), input_receipt_id, kind, fan_out_policy_version: Number(policy),
        outcome, produced: JSON.parse(String(produced)), conflict_fingerprints: null,
        source_implementation, runtime_route, source_identity, idempotency_key,
        created_at: new Date().toISOString(),
      };
      t.fanout_transition.push(row);
      return { rows: [{ id: row.id }] };
    }

    // ── parity ────────────────────────────────────────────────────────
    if (s.startsWith("SELECT wr_id, window_start")) {
      return { rows: t.parity_evidence.slice(0, Number(params[0] ?? 5)) };
    }
    if (s.startsWith("SELECT surface, count(*)::int AS mutations")) {
      const counts = new Map<string, number>();
      for (const r of t.mutation_provenance) {
        counts.set(r.surface, (counts.get(r.surface) ?? 0) + 1);
      }
      return {
        rows: [...counts.entries()]
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([surface, mutations]) => ({ surface, mutations })),
      };
    }

    throw new Error(`FakeDb: unrecognised statement: ${s.slice(0, 120)}`);
  }
}
