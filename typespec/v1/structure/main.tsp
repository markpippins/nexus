/**
 * Structure v0.1 — deterministic structural observations.
 *
 * Model-only contract per the architect ruling on the AST-parsing thread
 * (discussions thread 2ecc4700, record 143b0e04 — conditional concurrence):
 * Structure is a producer, not an authority. It emits source-anchored,
 * non-authoritative structural observations and serves Expression, Aspects,
 * Resolution, query tools and the blackboard through a neutral contract with
 * adapters — it imports no Expression semantics and owns no vocabulary.
 *
 * Two identities, per the ruling's required correction:
 *  - source_fact_id: canonical source identity + content hash + node path +
 *    fact kind — identifies the inspected source location, parser-independent.
 *  - observation_id: source_fact_id + parser identity/version + grammar
 *    revision + canonical payload + read-set fingerprint — identifies what a
 *    particular parser run claimed. A changed grammar revision creates a new
 *    observation population.
 *
 * Parse honesty: parse_status is (complete | partial | unsupported | failed);
 * non-complete parses carry diagnostics. Partial or unsupported parsing must
 * never turn absence of a fact into an assertion that the construct does not
 * exist. Facts never grant governed identity, never self-authorize relations,
 * and never invent free-form predicates — an AST fact that says "migration M
 * references table T" resolves through the governed vocabulary or becomes an
 * unresolved finding. State boundary is explicit:
 * structural_fact ≠ relation_candidate ≠ evaluated_relation ≠ admitted_relation.
 */
namespace org.nexus.structure.v1;

/** Kind of source artifact an observation was parsed from. */
alias SourceKind =
  | "sql_migration"
  | "sql_ddl"
  | "seed_sql"
  | "typespec"
  | "solscript"
  | "jvm_source"
  | "python_source"
  | "unknown";

/** What the parser recognized at an anchor. */
alias FactKind =
  | "table"
  | "column"
  | "foreign_key"
  | "named_constraint"
  | "index"
  | "enum_type"
  | "operation"
  | "seed_value"
  | "unsupported_syntax";

/**
 * Honesty of the parse that produced this observation. Non-complete parses
 * must carry diagnostics; absence of a fact is never evidence of absence.
 */
alias ParseStatus = "complete" | "partial" | "unsupported" | "failed";

/**
 * Authority is fixed by contract: Structure is a reader. An observation
 * claiming any other authority status fails conformance
 * (authority_escalation_denied).
 */
alias AuthorityStatus = "non_authoritative";

/**
 * Explicit candidate resolution status. Ambiguity and unmapped references
 * are recorded, never silently dropped and never silently resolved.
 */
alias CandidateStatus = "resolved" | "ambiguous" | "unmapped" | "unsupported";

/** Codes a Structure run may emit as findings. */
alias FindingCode =
  | "missing_anchor"
  | "parser_drift"
  | "grammar_drift"
  | "unsupported_syntax"
  | "stale_source_revision"
  | "authority_escalation_denied"
  | "identity_collision";

/**
 * Governed mapping slot for a referential observation. Mapping is
 * Resolution/Aspects work — Structure only records the state honestly:
 * "unmapped", or "mapped" with the governed relation id and evidence refs.
 * The parser must not invent free-form predicates.
 */
model GovernedRelationMapping {
  status: "unmapped" | "mapped";
  governed_relation_id?: string;
  /** References to the evidence that grounded a mapped relation. */
  evidence_refs?: string[];
}

/**
 * Identity and integrity of one parsed source artifact. Compatible with the
 * Expression SourceRef envelope: revision, content_hash and source_uri carry
 * the same meaning there. Bridging concerns (Structure→Expression) are
 * explicit (S4) and deliberately absent here.
 */
model StructureSource {
  source_uri: string;
  revision: string;
  /** sha256 hex digest of the exact bytes parsed. */
  content_hash: string;
  language: string;
  source_kind: SourceKind;
}

/** Pinned parser and grammar identity; drift against either is a finding. */
model ParserProvenance {
  parser_identity: string;
  parser_revision: string;
  grammar_revision: string;
}

/** Byte span of the observation within its source (1-based lines/columns). */
model NodeSpan {
  start_line: int32;
  start_column: int32;
  end_line: int32;
  end_column: int32;
}

/** Where in the parsed source the observation lives. */
model NodeAnchor {
  /** Canonical path within the parse tree, e.g. "ddl[3].create_table.users". */
  node_path: string;
  span?: NodeSpan;
}

/**
 * A diagnostic from a non-complete parse: the honest record of what the
 * parser could not do.
 */
model ParseDiagnostic {
  code: string;
  message: string;
  anchor?: NodeAnchor;
}

/**
 * One deterministic structural observation. Carries both identities:
 * source_fact_id (where in the source) and observation_id (what this run
 * claimed about it).
 */
model StructuralObservation {
  observation_id: string;
  source_fact_id: string;
  source: StructureSource;
  parser: ParserProvenance;
  anchor: NodeAnchor;
  fact_kind: FactKind;
  payload: Record<unknown>;
  parse_status: ParseStatus;
  /** Required when parse_status is not "complete". */
  diagnostics?: ParseDiagnostic[];
  /** Fingerprint of the run's full read set; observation identity includes it. */
  read_set_fingerprint: string;
  authority_status: AuthorityStatus;
  /**
   * Explicit unresolved-candidate status; absent means the observation is
   * fully resolved within Structure's own vocabulary.
   */
  candidate_status?: CandidateStatus;
  /** Governed mapping slot; absent means mapping not yet attempted. */
  relation_mapping?: GovernedRelationMapping;
}

/**
 * An explicit unresolved candidate: a recognized shape whose mapping is not
 * decided. Ambiguity is first-class output, not an error.
 */
model UnresolvedCandidate {
  candidate_id: string;
  source: StructureSource;
  anchor: NodeAnchor;
  reason: CandidateStatus;
  note: string;
}

/** A bounded finding from a run. */
model StructureFinding {
  finding_id: string;
  code: FindingCode;
  message: string;
  anchor?: NodeAnchor;
}

/** One entry of a run's read set: everything the run read, nothing more. */
model ReadSetEntry {
  source_uri: string;
  revision: string;
  content_hash: string;
  /** Why this source was read, e.g. "migration", "seed", "grammar". */
  role: string;
}

/** The record of one deterministic Structure execution. */
model StructureRun {
  run_id: string;
  contract_revision: string;
  parser: ParserProvenance;
  read_set: ReadSetEntry[];
  /**
   * sha256 over the canonical JSON of the read set sorted by
   * (source_uri, revision) under fingerprint v1 — replay-stable.
   */
  read_set_fingerprint: string;
  observations: StructuralObservation[];
  findings: StructureFinding[];
  unresolved: UnresolvedCandidate[];
  authority_status: AuthorityStatus;
}

/**
 * Language-neutral manifest shape. Mirrors typespec/v1/structure/
 * contract-manifest.json and python/structure/contract.py exactly; the
 * conformance suite pins the parity.
 */
model StructureContractManifest {
  contract_revision: string;
  fingerprint_version: int32;
  owner: "structure";
  authority_status: "non_authoritative";
  source_kinds: SourceKind[];
  fact_kinds: FactKind[];
  parse_statuses: ParseStatus[];
  relation_mapping_statuses: ("unmapped" | "mapped")[];
  candidate_statuses: CandidateStatus[];
  finding_codes: FindingCode[];
  source_fact_id_version: int32;
  observation_id_version: int32;
  read_set_fingerprint_algorithm: string;
  /** Expression-envelope compatibility terms (no implicit taxonomy growth). */
  expression_envelope: Record<unknown>;
  /** Authority boundary terms (producer-not-authority, per ruling 143b0e04). */
  authority_boundary: Record<unknown>;
  contract_fingerprint: string;
}
