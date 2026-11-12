/**
 * agenda — TypeSpec Models
 *
 * Contract for the deliberation surface represented by `nebula.agendas` and
 * `nebula.agenda_items` (design note docs/nebula-sol-absorption.md, phase 6).
 *
 * This is deliberately NOT `assembly.forum_agendas`. The forum agenda is a UI
 * grouping of threads inside one forum; this contract describes the object
 * that carries cohesion analysis, source items, decisions and open questions
 * across deliberations. The two are separate representations and must not be
 * collapsed by name.
 *
 * Model-only (calendar / write-queue precedent): no HTTP routes, no verbs.
 * Storage is a profile hint, not part of the wire contract — the canonical
 * PostgreSQL shape is JSONB metadata plus relational spine, and a MongoDB
 * instance store is permitted only as an alternative projection. TypeSpec has
 * no `@instanceStorage` decorator; inventing one would fail closed at compile
 * time, so the profile is recorded here and in the design note instead.
 *
 * Identity: `id` is the canonical key. An item's `sourceId` is a reference to
 * the artifact that surfaced it, never its identity.
 */
namespace org.nexus.agenda;

/**
 * The states the current deliberation store produces.
 *
 * Measured live on 2026-11-12: [draft, specified]. This union is therefore
 * exhaustive for the data that exists — adding a state is a contract change,
 * not an annotation. It is intentionally not widened to a speculative
 * lifecycle that no writer emits.
 */
union AgendaStatus {
  Draft: "draft",
  Specified: "specified",
}

/**
 * What surfaced an agenda item. Measured live: [requirement]. Kept open as a
 * string rather than a union because the item store treats source_type as an
 * extensible reference kind, and a new producer must not need a contract
 * revision to register an item.
 */
scalar AgendaItemSourceType extends string;

/**
 * A deliberation: the set of still-open items plus the planner's analysis of
 * their cohesion and overlap.
 */
model Agenda {
  @format("uuid")
  id: string;

  title: string;

  /** Scope the deliberation covers, in the planner's words. */
  scope?: string;

  status: AgendaStatus;

  /** Planner's cohesion score for the agenda as a whole. */
  cohesionScore?: decimal;

  /** Pairwise overlap analysis, shape owned by the planner. */
  overlapMatrix?: Record<unknown>;

  /** How many source artifacts contributed items. */
  sourceCount?: int32;

  plannerAnalysis?: string;

  plannerConflicts?: Record<unknown>;

  plannerGaps?: Record<unknown>;

  /** Storage-profile metadata (JSONB in PostgreSQL; document body in Mongo). */
  metadata?: Record<unknown>;

  createdAt?: utcDateTime;
  updatedAt?: utcDateTime;

  // Bitemporal window: validity is when the deliberation held, recording is
  // when this row learned it. A correction appends a version; it never
  // rewrites the prior one.
  validFrom?: utcDateTime;
  validUntil?: utcDateTime;
  recordedOn?: utcDateTime;
  recordedUntil?: utcDateTime;
}

/**
 * One item on an agenda. `included` is the planner's inclusion decision, not
 * a deletion: an excluded item stays on the record with its note.
 */
model AgendaItem {
  @format("uuid")
  id: string;

  @format("uuid")
  agendaId: string;

  /** Kind of artifact that surfaced this item (live: requirement). */
  sourceType?: AgendaItemSourceType;

  /** Identity of the surfacing artifact. A reference, not this item's id. */
  @format("uuid")
  sourceId?: string;

  title: string;

  body?: string;

  /** Decisions already taken on this item, as references or short text. */
  decisions?: string[];

  openQuestions?: string[];

  supportingRefs?: string[];

  /** Planner inclusion decision. False keeps the item and its note. */
  included?: boolean;

  plannerNote?: string;

  createdAt?: utcDateTime;
  updatedAt?: utcDateTime;

  validFrom?: utcDateTime;
  validUntil?: utcDateTime;
  recordedOn?: utcDateTime;
  recordedUntil?: utcDateTime;
}
