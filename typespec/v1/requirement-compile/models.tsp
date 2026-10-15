/**
 * Models for nexus.requirement.compile-to-plan/1.0.
 *
 * The receipt is evidence about compilation and optional pending-plan creation.
 * It is not an execution or admission receipt.
 */
namespace org.nexus.requirementcompile {
  using Http;

union CompilationOutcome {
  "compiled",
  "plan_created",
  "partial",
  "rejected",
  "failed",
}

union SelectionKind {
  "registry_match",
  "fallback",
}

model CompileRequirementRequest {
  /** Requirement identifier as accepted by the current Nebula route. */
  requirementId: string;
  /** Request creation of a pending implementation plan after compilation. */
  createPlan?: boolean = false;
  /** Produce an IR without requesting plan creation or downstream effects. */
  dryRun?: boolean = false;
  /** Optional non-empty deliverable description. */
  deliverable?: string | null;
  /** Caller-generated stable key for request deduplication (1–200 characters); required for plan creation. */
  idempotencyKey?: string;
}

model CrossReference {
  relationType: string;
  targetType: string;
  targetId: string;
  targetLabel?: string;
}

model RequirementSnapshot {
  requirementId: string;
  title: string;
  description?: string;
  status?: string;
  priority?: string;
  requirementType?: string;
  acceptanceCriteria: string[];
  hierarchy: Record<unknown>;
  crossReferences: CrossReference[];
  /** Null until Nebula exposes a stable source revision or fingerprint. */
  sourceRevision: string | null;
}

model RegistrySelection {
  kind: SelectionKind;
  entryId: string | null;
  intentId: string;
  version: string | null;
  /** The pattern actually selected, if a registry mapping matched. */
  matchedPattern: string | null;
  /** e.g. longest-regex-substring/v1; names method, not semantic validity. */
  selectionMethod: string;
  fallbackReason: string | null;
}

model OpcodeStep {
  step: int32;
  opcode: string;
  target: string;
  args: Record<unknown>;
  idempotencyKey: string;
}

model CompilationIr {
  opSequence: OpcodeStep[];
  filesAffected: string[];
  dependencies: string[];
  acceptanceCriteria: string[];
  deliverable?: string | null;
}

model CompilationEffect {
  dryRun: boolean;
  planRequested: boolean;
  planCreated: boolean;
  planNumber?: string | null;
  /** Plan state is pending when created by the existing POST /api/plans route. */
  planStatus: string | null;
  /** Always false: this capability does not authorize opcode execution. */
  executionAuthorized: false;
}

model CompilationWarning {
  code: string;
  message: string;
}

model CompilationError {
  code: string;
  message: string;
  retryable?: boolean;
}

model CompilationResult {
  outcome: CompilationOutcome;
  receiptId: string;
  requirement: RequirementSnapshot;
  selection: RegistrySelection;
  ir?: CompilationIr;
  effect: CompilationEffect;
  warnings: CompilationWarning[];
}

model CompileRequirementResponse {
  result: CompilationResult;
}

model CompilationEvidenceReference {
  kind: string;
  id: string;
  digest?: string | null;
}

/**
 * Append-only record of one compilation attempt. Store the canonical input and
 * IR (not only their optional digests) if replay and explanation are required.
 */
model CompilationReceipt {
  receiptSchema: "nexus.compilation-receipt/v1";
  receiptId: string;
  capabilityName: "nexus.requirement.compile-to-plan";
  capabilityVersion: "1.0";
  correlationId: string;
  occurredAt: utcDateTime;
  outcome: CompilationOutcome;
  actorRole: string;
  actorPrincipalId?: string | null;
  requirement: RequirementSnapshot;
  canonicalInput: CompileRequirementRequest;
  normalizedInputDigest: string;
  compilerService: "nebula-srv";
  compilerBuild?: string | null;
  algorithmVersion: string;
  selection: RegistrySelection;
  resultIr: CompilationIr | null;
  resultIrDigest: string | null;
  effect: CompilationEffect;
  journalRecordId: string | null;
  evidence: CompilationEvidenceReference[];
  warnings: CompilationWarning[];
  error: CompilationError | null;
  /** False when source/compiler identity is insufficient for deterministic replay. */
  replayable: boolean;
}

model CompilationReceiptResponse {
  receipt: CompilationReceipt;
}

model ApiError {
  code: string;
  message: string;
  correlationId?: string;
  receiptId?: string;
}

// HTTP wrappers keep protocol status distinctions explicit in generated OpenAPI.
model CompileSuccessHttpResponse {
  @statusCode _: 200 | 201;
  @body body: CompileRequirementResponse;
}

model BadRequestHttpResponse {
  @statusCode _: 400;
  @body body: ApiError;
}

model NotFoundHttpResponse {
  @statusCode _: 404;
  @body body: ApiError;
}

model ConflictHttpResponse {
  @statusCode _: 409;
  @body body: ApiError;
}

model UnprocessableHttpResponse {
  @statusCode _: 422;
  @body body: ApiError;
}

model InternalErrorHttpResponse {
  @statusCode _: 500;
  @body body: ApiError;
}

union CompileHttpResponse {
  CompileSuccessHttpResponse,
  BadRequestHttpResponse,
  NotFoundHttpResponse,
  ConflictHttpResponse,
  UnprocessableHttpResponse,
  InternalErrorHttpResponse,
}

model ReceiptFoundHttpResponse {
  @statusCode _: 200;
  @body body: CompilationReceiptResponse;
}

model ReceiptNotFoundHttpResponse {
  @statusCode _: 404;
  @body body: ApiError;
}

union ReceiptHttpResponse {
  ReceiptFoundHttpResponse,
  ReceiptNotFoundHttpResponse,
}
}
