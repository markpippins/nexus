/**
 * write-queue — TypeSpec Models
 *
 * Reconciliation-intent contract for the mobile write-queue. Reuses the
 * shared nats-envelope CanonicalEnvelope as the transport wrapper (same
 * serialization as every other Nexus event); this file models the
 * WRITE-QUEUE-SPECIFIC payload and lifecycle that ride inside it.
 *
 * Conceptual split (the whole point of a separate stream):
 *   - nexus.events      → canonical events describe what HAPPENED.
 *   - nexus.write-queue → entries describe what a disconnected or
 *     capability-poor context WANTS reconciled. An entry is an *attempt*
 *     to cause a canonical mutation, not a record that it occurred.
 *
 * The reconciler (cascade) turns:  mobile intent → reconciled canonical
 * fact/event. Reconciliation is NOT simple replay — the entry carries the
 * version/authority/schema context needed to classify every non-replay
 * outcome.
 */
import "../nats-envelope/python/models.tsp";

namespace org.nexus.writequeue {

  // ── Reconciliation lifecycle ────────────────────────────────────────

  /** Lifecycle of a write-queue entry as it moves toward reconciliation. */
  enum WriteQueueStatus {
    @doc("Queued by the mobile/disconnected context; not yet attempted.")
    Queued: "queued",

    @doc("Picked up by the reconciler; application in progress.")
    Reconciling: "reconciling",

    @doc("Applied and emitted as a canonical nexus.events event.")
    Reconciled: "reconciled",

    @doc("Applied partially — some sub-mutations landed, others did not.")
    PartiallyApplied: "partially_applied",

    @doc("Rejected; the entry will NOT be applied (see outcome classification).")
    Failed: "failed",

    @doc("Superseded by a later entry or decision; never applied.")
    Superseded: "superseded",
  }

  // ── Outcome classification (why reconciliation was NOT simple replay) ─

  /**
   * Non-replay reconciliation outcome. Every value here is a case where
   * "apply the queued write verbatim" is the WRONG answer — the reconciler
   * must classify before it can decide. Mirrors the operator's design list.
   */
  enum ReconciliationOutcome {
    @doc("The target's version is ahead of what the entry was based on; needs re-read.")
    StaleVersion: "stale_version",

    @doc("Two or more queued/intent mutations conflict; cannot both apply.")
    ConflictingMutation: "conflicting_mutation",

    @doc("The reconciling context lacks the capability the write requires.")
    MissingCapability: "missing_capability",

    @doc("Authorization changed between queue time and reconcile time.")
    AuthorizationChange: "authorization_change",

    @doc("The target schema evolved past what this entry was written against.")
    SchemaEvolution: "schema_evolution",

    @doc("A duplicate entry was submitted (same intent/idempotency key); dedup.")
    DuplicateSubmission: "duplicate_submission",

    @doc("Provenance could not be verified end-to-end for the entry.")
    ProvenanceVerification: "provenance_verification",

    @doc("Only part of the mutation applied; remainder needs retry or manual merge.")
    PartialApplication: "partial_application",

    @doc("Reconciliation finished and produced a result (see ReconciliationResult).")
    Result: "result",
  }

  // ── The write intent (what a disconnected context WANTS to happen) ──

  /**
   * The mutation the disconnected context wants reconciled. This is an
   * INTENT, not an event — nothing here has happened yet.
   *
   * `target` names the canonical surface the mutation is aimed at (e.g.
   * the resolution schema or the governed service). `verb` + `payload`
   * describe the intended change. `baseVersion` lets the reconciler detect
   * staleness/conflict; `requiredCapability` lets it detect missing
   * capability or authorization change.
   */
  model WriteIntent {
    /** Idempotency key — same intent resubmitted is a duplicate, not a second write. */
    writeId: string;

    /** Canonical target surface (e.g. "resolution.proposition", "broker.config"). */
    target: string;

    /** Mutation verb (e.g. "upsert", "transition", "update", "delete"). */
    verb: string;

    /** The intended mutation payload. */
    payload: Record<unknown>;

    /** Schema/contract version the intent was written against. */
    schemaVersion: string;

    /** The target's version the entry assumed at queue time (staleness check). */
    baseVersion?: string;

    /** Capability the reconciling context must hold to apply this write. */
    requiredCapability?: string;

    /** Actor identity attributes (echoed from the envelope). */
    actor?: Record<unknown>;
  }

  // ── Reconciliation result (what the reconciler decided/did) ─────────

  /**
   * The reconciler's decision about a write-queue entry. Carries the
   * classification when reconciliation was NOT a clean apply, plus a
   * link to the canonical event(s) emitted when it succeeded.
   */
  model ReconciliationResult {
    /** The entry this result belongs to. */
    writeId: string;

    /** Final lifecycle state. */
    status: WriteQueueStatus;

    /** Non-replay classification when applicable. */
    outcome: ReconciliationOutcome;

    /** Human/operator-readable detail. */
    detail: string;

    /** Canonical event id(s) emitted on nexus.events when applied. */
    producedEventIds?: string[];

    /** Reconciled version of the target after application. */
    resultingVersion?: string;

    /** ISO 8601 timestamp of the reconcile attempt. */
    reconciledAt: string;
  }

  // ── The full queue entry (envelope payload) ─────────────────────────

  /**
   * The payload carried inside a CanonicalEnvelope on the nexus.write-queue
   * stream. The envelope's `subject` is `nexus.write-queue.v1.<target>` and
   * its `payload` is this model. The reconciler drains the stream, classifies
   * via outcome, and emits canonical events on nexus.events.
   */
  model WriteQueueEntry {
    /** The intended mutation. */
    intent: WriteIntent;

    /** Idempotency / provenance echo for dedup + verification. */
    correlationId: string;

    /** Source context: which disconnected/capability-poor runtime queued this. */
    originComponent: string;

    /** ISO 8601 queue time. */
    queuedAt: string;

    /** Reconciliation decision (populated by the reconciler). */
    result?: ReconciliationResult;
  }

  // ── Stream / subject contract (documentation of the protocol surface) ─

  /**
   * Subject naming for the write-queue stream.
   *   nexus.write-queue.v1.<target>.<verb>
   *
   * Distinct from nexus.events (cascade workflow, voyager fs) so the queue
   * stream is a separate JetStream stream with its own lifecycle. Both carry
   * CanonicalEnvelope; the semantic split is stream/lifecycle, not format.
   */
  model WriteQueueSubjects {
    /** e.g. nexus.write-queue.v1.resolution.proposition.upsert */
    subject: string;
  }
}