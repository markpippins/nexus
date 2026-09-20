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
  source_kind: "harvest" | "harvest_candidate" | "source_observation";
  source_schema: string;
  source_id: string;
  source_content_hash: string;
  content: string;
  disposition: ObservationDisposition;
  authority_status: "non_authoritative";
}

model CompatibilityConflict {
  source_identity: string;
  observation_ids: string[];
  status: "conflict";
  reason: string;
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
  conflicts: CompatibilityConflict[];
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
  Unevaluable: "unevaluable",
  Stale: "stale",
  Refused: "refused",
}

model EvaluationRequest {
  contract_revision: string;
  source_fingerprint: string;
  transcript_id: string;
  proposition: PropositionCandidate;
  read_set?: Record<unknown>;
  evaluator_revision: string;
  ontology_revision: string;
  authority_owner: string;
  authority_status: "evaluation_only";
  request_fingerprint: string;
}

model EvaluationResult {
  proposition_id: string;
  disposition: EvaluationDisposition;
  reason_code: string;
  reason?: string;
  request_fingerprint: string;
}

model EvaluationEnvelope {
  contract_revision: string;
  source_fingerprint: string;
  evaluator_revision: string;
  ontology_revision: string;
  authority_owner: string;
  authority_status: "evaluation_only";
  results: EvaluationResult[];
  evaluation_fingerprint: string;
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
