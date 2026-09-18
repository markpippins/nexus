/**
 * calendar — TypeSpec Models
 *
 * Contract for the Calendar primitive and the Session object it anchors
 * (design post a330914e, operator directive: synchronized multi-model work
 * with scheduled, co-incident context).
 *
 * Model-only (write-queue precedent): no HTTP routes, no verbs. The contract
 * describes the accumulation/consolidation vocabulary every runtime shares:
 *
 *   - Calendar          → accumulation surface (per-machine local OR shared)
 *   - CalendarEvent     → the atom: scheduled intent, local witness, or
 *                         heard-from-elsewhere observation
 *   - Session           → the co-incident context a window of events anchors
 *
 * Design decisions encoded here (design post section 4):
 *   Q2  deterministic event ids when machine-timer-emitted, so consolidation
 *       dedupes without coordination (idempotent re-application);
 *   epistemic kind  → scheduled/occurred/observed (V174 unreachable-vs-absent
 *       discipline: a meeting scheduled but never occurred is a fact, not an
 *       error);
 *   extendable windows → `end` optional; extension appends, never mutates;
 *   refs-only context  → ContextBundle carries opaque references (V167/V169
 *       stance), never inlined payloads.
 *
 * Storage and envelope wiring are deliberately absent: the DDL pre-stage
 * waits on roundtable Q1/Q2, and CanonicalEnvelope integration is an
 * emitter-slice (Q3) decision. This file is the shared vocabulary only.
 */

namespace org.nexus.calendar {

  // ── Epistemic kind ──────────────────────────────────────────────────

  /**
   * Epistemic kind of an event (V174 discipline applied to time).
   * Every value is a fact; none is an error state.
   */
  enum CalendarEventKind {
    @doc("Intent: scheduled in advance, may or may not occur.")
    Scheduled: "scheduled",

    @doc("Witnessed locally: this machine observed it happening.")
    Occurred: "occurred",

    @doc("Heard from elsewhere: consolidated from another machine's calendar.")
    Observed: "observed",
  }

  // ── References ──────────────────────────────────────────────────────

  /**
   * A participant reference: a role, a model, a session id, or a machine.
   * At least one field should be set; resolution/precedence across fields
   * is runtime policy, deliberately not fixed in the contract.
   */
  model AgentRef {
    /** Role name, e.g. "dba", "engineer". */
    role?: string;

    /** Model id, e.g. "freebuff/buffy", "opencode/big-pickle" (census `model` field). */
    @encodedName("application/json", "model")
    modelId?: string;

    /** Live session id (timeclock/harness/freebuff session identity). */
    @format("uuid")
    sessionId?: string;

    /** Hostname, for remote participants. */
    machine?: string;
  }

  /** Where an event came from: the machine and the emitter that produced it. */
  model EventSource {
    /** Hostname of the emitting machine. */
    machine: string;

    /** Emitter id: systemd unit name, boot-shim step, harness, API client. */
    emitter: string;
  }

  /**
   * Event window. `end` optional by design: extendable duration is native —
   * extension appends (a later event), it never mutates a recorded window.
   */
  model EventWindow {
    start: utcDateTime;
    end?: utcDateTime;
  }

  /** Append-only provenance for consolidation. */
  model EventProvenance {
    recordedAt: utcDateTime;
    recordedBy: string;

    /** Source event ids folded in during consolidation, when consolidated. */
    consolidatedFrom?: string[];
  }

  // ── The atom ────────────────────────────────────────────────────────

  /**
   * CalendarEvent — the atom of accumulation.
   *
   * Deterministic identity (Q2): when emitted from a machine timer, eventId
   * is derived from (source.machine, source.emitter, window.start) so the
   * same scheduled occurrence re-emitted on re-run carries the SAME id and
   * consolidation dedupes without coordination.
   */
  model CalendarEvent {
    /** Deterministic when machine-timer-emitted; see model doc. */
    @format("uuid")
    eventId: string;

    /** The calendar this event accumulates into. */
    @format("uuid")
    calendarId: string;

    /** Epistemic kind — see CalendarEventKind. */
    kind: CalendarEventKind;

    source: EventSource;
    title: string;
    window: EventWindow;

    /** Roles/models/sessions participating; may be empty for machine-only events. */
    participants: AgentRef[];

    /** The Session this event anchors, when it anchors one. */
    @format("uuid")
    sessionRef?: string;

    /** Kind-specific payload: recurrence rules, digests, outcomes. */
    payload?: Record<unknown>;

    provenance: EventProvenance;
  }

  // ── The accumulation surface ────────────────────────────────────────

  /** Scope of a calendar: one machine's local rhythm, or a shared gathering. */
  enum CalendarScope {
    @doc("Per-machine accumulation (timers, boot shim, local sessions).")
    Local: "local",

    @doc("Shared/meeting calendar that multiple machines contribute to.")
    Shared: "shared",
  }

  /** Calendar — the accumulation surface events collect into. */
  model Calendar {
    @format("uuid")
    calendarId: string;

    title: string;
    scope: CalendarScope;

    /** Owning machine hostname for local scopes; absent for shared. */
    owner?: string;
  }

  // ── The co-incident context ─────────────────────────────────────────

  /** Where agents join or start up. */
  model SessionAddress {
    /** Hostname or IP the Session is reachable at. */
    host: string;
    port: int32;
  }

  /**
   * Opening context for a Session — refs only (V167/V169 stance): opaque
   * references into session_context_snapshots, continuity digests, keychains,
   * and procedure cards. Payloads are resolved at adoption time, never
   * inlined here.
   */
  model ContextBundle {
    /** nebula.session_context_snapshots snapshot ids. */
    snapshotRefs: string[];

    /** Continuity digest record ids. */
    digestRefs: string[];

    /** Keychain references. */
    keychains: string[];

    /** Procedure card slugs visible to the Session. */
    procedureCards: string[];
  }

  /** Reconciliation lifecycle of a Session (close-then-insert discipline). */
  enum ReconcileState {
    @doc("Window open; events accumulating.")
    Open: "open",

    @doc("Window closed; awaiting fold-back into the canonical store.")
    Closed: "closed",

    @doc("Folded back: events, connection records, and minutes reconciled.")
    Reconciled: "reconciled",
  }

  /**
   * Session — a shared, co-incident context with temporal boundaries and its
   * own address. Joiners attach existing processes; starters spawn new ones;
   * both write connection records bound to this session. On window close the
   * session's events and records reconcile into the primary store — a Meeting
   * is a Session whose reconciliation produces a minutes record; a Schedule
   * is a Calendar with recurring events.
   *
   * Extension semantics: extending the window appends an event; the recorded
   * Session is never mutated.
   */
  model Session {
    @format("uuid")
    sessionId: string;

    title: string;

    /** ip/port where agents join or services start (Q5: ownership open). */
    address: SessionAddress;

    /** Temporal boundaries; extendable — see EventWindow. */
    window: EventWindow;

    participants: AgentRef[];

    /** Opening context, refs-only — see ContextBundle. */
    contextBundle: ContextBundle;

    /** The calendar this session's events accumulate into. */
    @format("uuid")
    calendarId: string;

    reconcileState: ReconcileState;
  }
}
