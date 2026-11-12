/**
 * workrequest — TypeSpec Models
 *
 * Canonical contract for the WorkRequest domain object (design note
 * docs/nebula-sol-absorption.md, phase 7). It describes
 * `resolution.work_request` as it exists today and is deliberately NOT one of
 * the wire DTOs: `losm-host`'s `WorkRequestRow`/`WorkRequestResponse` model a
 * different surface (`id int32` + `wr_id`), and `vision-srv`'s
 * `WorkRequestResponse` is a legacy HTTP body. Neither is the domain shape.
 *
 * Physical home: `resolution.work_request` (46 rows on 2026-11-12) is a
 * TEMPORARY home. The plan's rule is that operational data lives in `nebula`,
 * and the eventual table is `nebula.work_requests` (today a 4-row view over
 * history). That move is explicitly deferred: no dual-write, no third table.
 * `vision.canonical_work_requests` (V170) is withdrawn; its relational-spine +
 * `payload jsonb` shape is the template for the move, which is why the payload
 * column is described here but not modelled as a second storage contract.
 *
 * Model-only (calendar / agenda precedent): no HTTP routes, no verbs.
 */
namespace org.nexus.workrequest;

/**
 * The business status the current store produces.
 *
 * Measured live on 2026-11-12: [DRAFT, COMPLETED, CANCELLED] across 46 rows.
 * A new status is a contract change, not an annotation.
 */
union WorkRequestStatus {
  Draft: "DRAFT",
  Completed: "COMPLETED",
  Cancelled: "CANCELLED",
}

/**
 * A unit of requested work: the durable intent plus the operational spine the
 * pipeline keys on.
 */
model WorkRequest {
  @format("uuid")
  id: string;

  /**
   * Optional link into the asset envelope. Nullable because not every
   * WorkRequest has been admitted through the asset path.
   */
  @format("uuid")
  assetId?: string;

  title: string;

  description?: string;

  /** Requested outcome in the requester's words. */
  intent?: string;

  /** Structured request context. NOT NULL with an empty-object default. */
  context: Record<unknown>;

  /** Hard constraints the execution must respect. NOT NULL, empty by default. */
  constraints: Record<unknown>;

  @format("uuid")
  sourceSpecificationId?: string;

  @format("uuid")
  sourceRequirementId?: string;

  /** Plan reference. Text, not a uuid, in the current spine. */
  planId?: string;

  /** Legacy import identity; kept for provenance, never an addressable key. */
  legacyId?: string;

  businessStatus: WorkRequestStatus;

  createdBy?: string;

  createdAt: utcDateTime;
  updatedAt: utcDateTime;

  /** Set when the request is consumed by execution; null while pending. */
  consumedAt?: utcDateTime;

  // Bitemporal window: validity is when the request held, recording is when
  // this row learned it. A correction appends a version; it never rewrites the
  // prior one.
  validFrom?: utcDateTime;
  validUntil?: utcDateTime;
  recordedOn?: utcDateTime;
  recordedUntil?: utcDateTime;
}

/**
 * The eventual `nebula` payload envelope, recorded as a shape rather than a
 * storage decision.
 *
 * The deferred move keeps the normalized identity/authority/temporal spine and
 * puts the type-conforming instance data in ONE `payload jsonb` column, with
 * only the foreign keys that queries actually need lifted out. Until that move
 * is authorised, `resolution.work_request`'s separate `intent`/`context`/
 * `constraints` columns are the storage contract; this model exists so the
 * target shape is reviewed now instead of invented during the migration.
 */
model WorkRequestPayload {
  intent?: string;
  context: Record<unknown>;
  constraints: Record<unknown>;

  /** Useful relational spine the payload does not repeat. */
  @format("uuid")
  sourceSpecificationId?: string;

  @format("uuid")
  sourceRequirementId?: string;

  planId?: string;
}
