/**
 * Aspects v0.1 — governed tag vocabulary and bindings.
 *
 * Model-only contract. Aspects is the governed binding layer: it owns the
 * governed tag vocabulary and the bindings from Expression's projected tags
 * into that vocabulary. Expression remains a non-authoritative producer;
 * projected tags never carry governed identity until a binding is approved
 * here (review record bc724f6b, findings 1-2; remediation G6).
 */
namespace org.nexus.aspects.v1;

/**
 * Legal binding lifecycle statuses. Mirrors BINDING_STATUSES in
 * python/aspects/binding_port.py exactly (Aspect G2 machine).
 */
alias BindingStatus = "proposed" | "approved" | "rejected" | "expired";

/** A governed tag vocabulary entry (aspects.governed_tag_vocabulary). */
model GovernedTagVocabularyEntry {
  id: string;
  name: string;
  normalized_name: string;
  member_kind: string;
  definition: string;
  applies_to: string;
  parent_id?: string;
  ratified_by?: string;
  notes?: string;
  created_at: utcDateTime;
  expired_at?: utcDateTime;
}

/** A binding from an Expression projected tag into governed vocabulary
 * (persisted in aspects.tag_binding). */
model TagBinding {
  id: string;
  governed_tag_id: string;
  governed_tag_name: string;
  governed_normalized_name: string;
  member_kind: string;
  applies_to: string;
  source_identity: string;
  source_revision: string;
  /** Source namespace; backtick-escaped TypeSpec keyword. */
  `namespace`: string;
  tag_key: string;
  normalized_value: string;
  expression_observation_id?: string;
  status: BindingStatus;
  bound_by?: string;
  /** Who made the approve/reject decision; present exactly when status is
   * approved or rejected (decision-coherence CHECK in V199). Expiry is
   * lifecycle, not a decision, so expired rows carry none. */
  decided_by?: string;
  decided_at?: utcDateTime;
  /** Free-text rationale recorded with the decision (governance surface). */
  decision_note?: string;
  created_at: utcDateTime;
  expired_at?: utcDateTime;
}

/**
 * The projected side of the boundary, as produced by Expression. Projected
 * tags carry authority_status "projected" and never a pre-populated
 * governed_tag_id; the binding layer is the only path to governed identity.
 */
model ProjectedTagObservationInput {
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
}

/** Aggregate binding outcome carried alongside a projected tag bundle. */
model BindingSummary {
  total: int32;
  by_status: Record<int32>;
}

/**
 * Lifecycle transitions ratified by the G2 machine. The authoritative,
 * test-parity source is contract-manifest.json (binding_lifecycle) and
 * ALLOWED_TRANSITIONS in python/aspects/binding_port.py; this model is the
 * shape witness for contract consumers.
 */
model BindingLifecycle {
  proposed: ("approved" | "rejected" | "expired")[];
  approved: "expired"[];
  rejected: "expired"[];
  expired: never[];
}

/** Language-neutral Aspects contract manifest shape. */
model AspectsContractManifest {
  contract_revision: string;
  fingerprint_version: int32;
  binding_owner: "aspects";
  binding_statuses: BindingStatus[];
  binding_lifecycle: Record<string[]>;
  vocabulary_table: string;
  binding_table: string;
  projected_tag_authority_status: "projected";
  authority_boundary: Record<unknown>;
  contract_fingerprint: string;
}
