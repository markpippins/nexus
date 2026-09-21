/**
 * Expression v0.1 — transcript observations and governed graph candidates.
 *
 * Model-only contract. Expression produces source-linked observations and
 * candidates; it does not grant authority or perform admission.
 */
namespace org.nexus.expression.v1;

enum ObservationDisposition {
  Unreviewed: "unreviewed",
  AcceptedAsObservation: "accepted_as_observation",
  Rejected: "rejected",
  Superseded: "superseded",
  Disputed: "disputed",
  InsufficientEvidence: "insufficient_evidence",
}

enum Modality {
  Asserted: "asserted",
  Reported: "reported",
  Hypothetical: "hypothetical",
  Conditional: "conditional",
  Questioned: "questioned",
  Negated: "negated",
}

enum ObservationKind {
  EntityMention: "entity_mention",
  EventMention: "event_mention",
  RelationMention: "relation_mention",
  ClaimMention: "claim_mention",
  SpeechAct: "speech_act",
  Interaction: "interaction",
  DecisionMention: "decision_mention",
  Contradiction: "contradiction",
  Reference: "reference",
  Version: "version",
  OntologyTermCandidate: "ontology_term_candidate",
}

/**
 * Expression iterates its observation vocabulary conservatively.
 * New kinds should be added here before new extractor behavior is claimed.
 */
enum ExpressionTaxonomyStatus {
  ActiveExplicit: "active_explicit",
  Proposed: "proposed",
  Deferred: "deferred",
}

model TaxonomyEntry {
  kind: ObservationKind;
  status: ExpressionTaxonomyStatus;
  authority_expectation: "non_authoritative";
  disposition_expectation: "unreviewed";
  interpretation_note: string;
}

/**
 * Current POC taxonomy. Future slices should keep this in sync with extractor
 * and evaluator behavior rather than discovering vocabulary implicitly.
 */
model ExpressionTaxonomy {
  contract_revision: string;
  entries: TaxonomyEntry[];
}

model SourceRef {
  asset_id: string;
  revision?: string;
  content_hash: string;
  source_uri?: string;
  recorded_at: utcDateTime;
  as_of?: utcDateTime;
}

model TranscriptSegment {
  segment_id: string;
  transcript_id: string;
  ordinal: int32;
  speaker?: string;
  role?: string;
  text_hash: string;
  start_offset?: int32;
  end_offset?: int32;
  parent_segment_id?: string;
  source: SourceRef;
}

model ExtractionRun {
  run_id: string;
  input_fingerprint: string;
  extractor_revision: string;
  model_id?: string;
  contract_revision: string;
  ontology_revision?: string;
  started_at: utcDateTime;
  completed_at?: utcDateTime;
}

model CandidateLink {
  mention_id: string;
  candidate_id: string;
  method: string;
  score?: float64;
  status: "proposed" | "confirmed" | "rejected" | "ambiguous" | "disputed";
  evidence?: string[];
}

model ExpressionObservation {
  observation_id: string;
  kind: ObservationKind;
  source_segments: TranscriptSegment[];
  extraction_run: ExtractionRun;
  payload: Record<unknown>;
  confidence?: float64;
  disposition: ObservationDisposition;
  modality?: Modality;
  candidate_links?: CandidateLink[];
  valid_from?: utcDateTime;
  valid_to?: utcDateTime;
}

model PropositionCandidate {
  proposition_id: string;
  subject_ref: string;
  predicate: string;
  object_ref?: string;
  source_observation_ids: string[];
  supporting_observation_ids?: string[];
  conflicting_observation_ids?: string[];
  modality: Modality;
  status: "candidate" | "evaluated" | "disputed" | "superseded";
  required_read_set?: string;
}

model ExpressionBoundary {
  boundary_revision: string;
  source_of_truth: string;
  expression_role: string;
  observation_storage: "source" | "staging" | "canonical" | "projection";
  observation_regenerable: boolean;
  canonical_identity_store: string;
  canonical_lineage_store: string;
  canonical_disposition_store: string;
  evaluation_authority: string;
  graph_role: string;
  harvest_compatibility: "consume" | "converge" | "supersede";
  authority_status: "non_authoritative";
  supersession_record?: string;
}

model ProjectedTagObservation {
  tag_observation_id: string;
  source_identity: string;
  source_revision: string;
  tag_namespace: string;
  key: string;
  raw_value: unknown;
  normalized_value: string;
  kind: "source_tag" | "projected_metadata";
  status: "observed";
  authority_status: "projected";
  governed_tag_id?: string;
  basis: string;
  source_schema?: string;
  source_content_hash?: string;
}

model ProjectedTagConflict {
  source_identity: string;
  source_revision: string;
  tag_namespace: string;
  key: string;
  observation_ids: string[];
  status: "conflict";
  reason: string;
}

model CompatibilityObservation {
  observation_id: string;
  source_identity: string;
  source_kind: "harvest" | "harvest_candidate" | "source_observation" | "cross_reference";
  source_schema: string;
  source_id: string;
  source_content_hash: string;
  source_revision: string;
  source_status?: string;
  content: string;
  disposition: ObservationDisposition;
  authority_status: "non_authoritative";
  supersedes_source_identity?: string;
}

model CompatibilityIdentityCandidate {
  mention_id: string;
  candidate_id?: string;
  method: "stable_source_id" | "registered_alias" | "identity_collision";
  status: "proposed" | "ambiguous";
  evidence: string[];
}

model CompatibilityUnresolvedIdentity {
  mention_id?: string;
  source_identity?: string;
  status: "unresolved";
  reason: string;
}

model CompatibilityConflict {
  source_identity: string;
  observation_ids: string[];
  hashes: string[];
  status: "conflict";
  reason: string;
}

model CompatibilitySupersession {
  newer_source_identity: string;
  older_source_identity: string;
  status: "declared" | "dangling";
  evidence_observation_id: string;
}

model TagProjectionBundle {
  contract_revision: string;
  adapter_revision: string;
  observations: ProjectedTagObservation[];
  conflicts: ProjectedTagConflict[];
  authority_status: "non_authoritative";
  governed_tag_vocabulary_revision?: string;
  boundary: ExpressionBoundary;
}

model CompatibilityBundle {
  contract_revision: string;
  adapter_revision: string;
  observations: CompatibilityObservation[];
  identity_candidates: CompatibilityIdentityCandidate[];
  unresolved_identities: CompatibilityUnresolvedIdentity[];
  conflicts: CompatibilityConflict[];
  supersession_links: CompatibilitySupersession[];
  authority_status: "non_authoritative";
  boundary: ExpressionBoundary;
}

model ExpressionBundle {
  contract_revision: string;
  source_fingerprint: string;
  transcript_id: string;
  segments: TranscriptSegment[];
  observations: ExpressionObservation[];
  candidate_links: CandidateLink[];
  proposition_candidates: PropositionCandidate[];
  authority_status: "non_authoritative";
  boundary: ExpressionBoundary;
}

enum EvaluationDisposition {
  Asserted: "asserted",
  Rejected: "rejected",
  Disputed: "disputed",
  InsufficientEvidence: "insufficient_evidence",
  Pending: "pending",
  Unevaluable: "unevaluable",
  Refused: "refused",
  Uncertain: "uncertain",
  Advisory: "advisory",
  Stale: "stale",
}

model EvaluationRequest {
  contract_revision: string;
  source_fingerprint: string;
  transcript_id: string;
  proposition: PropositionCandidate;
  read_set?: Record<unknown>;
  read_set_fingerprint?: string;
  evaluator_revision: string;
  ontology_revision: string;
  authority_owner: string;
  authority_status: "evaluation_only";
  mutation_policy: "forbidden";
  request_fingerprint: string;
}

model EvaluationResult {
  proposition_id: string;
  disposition: EvaluationDisposition;
  reason_code?: string;
  reason?: string;
  authority_status: "evaluation_only" | "advisory";
  request_fingerprint?: string;
}

model EvaluationEnvelope {
  contract_revision: string;
  source_fingerprint: string;
  evaluator_revision: string;
  ontology_revision: string;
  authority_owner: string;
  read_set_fingerprint?: string;
  authority_status: "evaluation_only";
  mutation_policy: "forbidden";
  results: EvaluationResult[];
  evaluation_fingerprint: string;
}

/** The exact source span shape emitted by the deterministic v0.1 extractor. */
model CanonicalSourceRef {
  segment_id: string;
  transcript_id: string;
  text_hash: string;
  start_turn: int32;
  end_turn: int32;
}

/** The E1 canonical segment shape; text remains source-linked staging material. */
model CanonicalTranscriptSegment {
  segment_id: string;
  transcript_id: string;
  ordinal: int32;
  role?: string;
  text: string;
  text_hash: string;
  start_turn: int32;
  end_turn: int32;
  turn_count: int32;
  boundary_reason: string;
}

/** Canonical active observation shape; no authority is implied. */
model CanonicalExpressionObservation {
  observation_id: string;
  kind: "reference" | "version" | "speech_act";
  value: string;
  source: CanonicalSourceRef;
  extractor_revision: string;
  input_fingerprint: string;
  reference_kind?: string;
  verb?: string;
  disposition: "unreviewed";
  modality: Modality;
  temporal_scope?: Record<unknown>;
  authority_status: "non_authoritative";
}

/** Candidate links are intentionally narrower than future governed bindings. */
model CanonicalCandidateLink {
  mention_id: string;
  candidate_id: string;
  method: string;
  status: "proposed" | "ambiguous";
  evidence: string[];
}

/** Free-form predicate tokens remain unresolved until the relation gate. */
model CanonicalPropositionCandidate {
  proposition_id: string;
  subject_ref: string;
  predicate: "mentions_decision_language";
  predicate_status: "unresolved";
  object_ref?: string;
  source_observation_ids: string[];
  modality: Modality;
  status: "candidate";
  required_read_set?: Record<unknown>;
}

model ExpressionContractManifest {
  contract_revision: string;
  fingerprint_version: int32;
  authority_status: "non_authoritative";
  observation_storage: "staging";
  observation_regenerable: true;
  active_observation_kinds: string[];
  active_candidate_link_statuses: string[];
  candidate_predicate_status: "unresolved";
  aspects_boundary: Record<unknown>;
  canonical_owners: Record<unknown>;
  contract_fingerprint: string;
}

/** Canonical E1 envelope used for Python/TypeScript reconciliation. */
model CanonicalExpressionBundle {
  contract_revision: string;
  contract_fingerprint: string;
  source_fingerprint: string;
  transcript_id: string;
  segments: CanonicalTranscriptSegment[];
  observations: CanonicalExpressionObservation[];
  candidate_links: CanonicalCandidateLink[];
  proposition_candidates: CanonicalPropositionCandidate[];
  authority_status: "non_authoritative";
  boundary: ExpressionBoundary;
}

model GraphProjection {
  projection_id: string;
  generation: string;
  source_run_id: string;
  source_fingerprint: string;
  node_count: int32;
  edge_count: int32;
  round_trip_verified: boolean;
  rollback_of?: string;
}

/** E5 compact receipt retained by Resolution; it is not an authority grant. */
model CanonicalEvaluationReceipt {
  receipt_id: string;
  source_namespace: string;
  source_run_id: string;
  target_id: string;
  evaluation_fingerprint: string;
  request_fingerprint?: string;
  disposition: EvaluationDisposition;
  reason_code?: string;
  authority_status: "evaluation_only";
  canonical_owner: "resolution";
  recording_policy: "receipt_only";
}

model KeychainEvaluationContext {
  schema_version: int32;
  checkpoint_class: "evaluation";
  source_run_id: string;
  source_fingerprint: string;
  read_set_fingerprint?: string;
  evaluator_revision: string;
  ontology_revision: string;
  authority_owner: string;
  source_refs: Record<unknown>[];
  retention_class: string;
  source_content_stored: false;
  authority_status: "context_only";
  context_fingerprint: string;
}

model E5RollbackLineage {
  status: "not_applicable" | "rolled_back";
  rollback_of?: string;
  rollback_id?: string;
  reason?: string;
  lineage: string[];
}

/** Bounded E5 artifact: canonical receipt plus regenerable projections. */
model ExpressionE5Artifact {
  e5_revision: string;
  source_fingerprint: string;
  source_run_id: string;
  canonical_receipts: CanonicalEvaluationReceipt[];
  graph_projection: Record<unknown>;
  keychain_context: KeychainEvaluationContext;
  rollback: E5RollbackLineage;
  write_policy: Record<unknown>;
  artifact_fingerprint: string;
}
