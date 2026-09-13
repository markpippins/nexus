# TypeSpec → Resolution Concept Mapping

Generated from `typespec/v1/**/models.tsp` (485 models, 56 files, 37 service namespaces).

**Input pin (architect correction d2627976):** exactly the 56 `models.tsp` files committed at `cb46ec55` (2026-09-03). `aegis-srv` (entered tree 09-05) is explicitly excluded from this render; its vocabulary waits for a later amendment bump per the Amendment 3 protocol. Full literal file list is recorded in the freeze package alongside this document's hash.

## Methodology

- **DOMAIN** models → `resolution.concept` (the vocabulary).
- **ENVELOPE** models → `resolution.representation` / comparison targets, NOT concepts. ENVELOPE = transport/DTO wrappers matching: `*Response`, `*Request`, `*Result`, `*Counts`, `*Info`, `*Envelope` (transport only — see explicit dispositions below), `*List`, `*Status`, `Paged*`, `Error*`, `Health*`, `*Meta`, generic `PagedResponse<T>`, plus the named set {ErrorResponse, PagedMeta, PagedResponse, BinaryData, PaginatedResult, Counts, HealthResponse, ErrorDetail, GraphEdge, AuditGraph, PlanStatus, CpfItem}.
- **Explicit envelope-named dispositions** (architect correction d2627976 — these share the `*Envelope` suffix but differ in status, so each is dispositioned, not silently dropped):
  - `GovernanceEnvelope` (governance-envelope) → **DOMAIN** (the governed contract itself, not transport; carries contract/semantic/workflow/law/evaluation).
  - `CanonicalEnvelope` (nats-envelope) → **ENVELOPE** (NATS transport envelope for cascade/voyager/vision/conduit events; its eventId/eventType are transport identity for routing/dedup, not domain-concept identity; the event *payloads* are the domain concepts).
  - `QuarantineEnvelope` (nexus-tools) → **ENVELOPE** (quarantine wrapping shape, transport).
  - `CompressionEnvelope`, `CausalityEnvelope` (ccnf-verifier) → **ENVELOPE** (verifier-internal wrapping shapes, transport).
- **DOMAIN exceptions** (curated set of operation/contract-adjacent names kept as DOMAIN because they name governed entities, not transport): ExecutionLease/Attempt/Receipt/Request, CascadeEvent, ReplayEvent, Snapshot, RuntimeState, StateDelta, Session, Workspace, Requirement, System, Subsystem, Feature, AgentRecord, HarvestCandidate, OpenQuestion, Role, Observation, Assessment, ImplementationPlan, FrameValue, LawSnapshot, GovernanceContract, InputSnapshot, GovernanceEvaluation, AssertionResult, Violation, GateResult, AuthorityEntry, WorkflowEdge, ProjectionProposition, ApiEndpoint, PebAdmissionResult, AdmissionResult, ThreadComment, VectorExpected, StateDeltaWrites, IdentityResponse, TaskSummary, RunEvents, ActiveSession, SearchResultItem, WorkerEntry, KeychainTriggerEvent, KeychainActiveInstance, PtySession, HarnessSession, WorkflowContext, SemanticIdentity, TableInfo/ColumnInfo/IndexInfo/ViewInfo/TriggerInfo/ProcedureInfo (schema-introspection entities), FsItem/FsEntry/FsListItem/FsItemReference.
- Reference-typed fields (a model field whose type is another model) → `resolution.concept_relationship` (146 edges), not inline attributes.
- Scalar fields → `resolution.concept_attribute` with `value_type` mapping: string→text, int32/int64→integer, float64→numeric, boolean→boolean, utcDateTime→timestamp, unknown/Record→jsonb.
- Cross-service name collisions are preserved as **explicit findings**, not silently merged (analyst contract e450a8a7). The 28 models classified by the extended suffixes (`*Counts/*Info/*Envelope/*List/*Status`) contribute zero DOMAIN collisions — verified: none appear in the collision inventory.

## Classification totals

- DOMAIN (→ concept): **316**
- ENVELOPE (→ representation): **169**

## DOMAIN models → concepts, by service

### assembly-srv (7)

- `Agenda` — id:string, title:string, body:string, forumId:string, sortOrder:int32, createdAt:string
- `AgendaItem` — id:string, agendaId:string, title:string, body:string, sortOrder:int32, createdAt:string
- `BridgeArtifactRef` — id:string, postId:string, artifactType:string, artifactId:string
- `Forum` — id:string, slug:string, name:string, description:string, sortOrder:int32, createdAt:string
- `ForumThread` — id:string, forumId:string, title:string, body:string, statusRating:int32, postedById:string, role:string, createdAt:string
- `ThreadComment` — id:string, threadId:string, body:string, postedById:string, parentId:string, role:string, statusRating:int32, createdAt:string
- `ThreadDetail` — author:{ id: string, comments:ThreadComment[], commentCount:int32

### atlas (3)

- `GraphView` — id:int64, name:string, description:string, cameraPositionX:float64 = 0.0, cameraPositionY:float64 = 40.0, cameraPositionZ:float64 = 120.0, cameraTargetX:float64 = 0.0, cameraTargetY:float64 = 15.0, cameraTargetZ:float64 = 0.0, camera2PositionX:float64 = 0.0, camera2PositionY:float64 = 40.0, camera2PositionZ:float64 = 120.0…
- `GraphViewConnection` — id:int64, sourceNodeId:string, targetNodeId:string, direction:ConnectionDirection, createdAt:utcDateTime, updatedAt:utcDateTime
- `GraphViewPosition` — id:int64, nodeId:string, positionX:float64 = 0.0, positionY:float64 = 0.0, positionZ:float64 = 0.0, label:string, description:string, color:string, createdAt:utcDateTime, updatedAt:utcDateTime

### cascade-srv (3)

- `CascadeEvent` — id:string, eventType:string, sourceService:string, sourceId:string, payload:unknown, correlationId:string, parentEventId:string, createdAt:string
- `EventLineage` — event:CascadeEvent, parent:CascadeEvent, children:CascadeEvent[]
- `Subscriber` — id:string, pattern:string, role:string, channel:string, status:string, lastRunAt:string, nextRunAfter:string, config:unknown, createdAt:string

### ccnf-ref (13)

- `Cer` — event_id:string, event_version:int32, ccnf_version:int32, system:string, domain:string, timestamp:int64, actor:Record<unknown>, intent:Record<unknown>, identity:CerIdentity, causality:Record<unknown>, artifact_refs:string[], state_delta:StateDelta[]…
- `CerIdentity` — entity_key:string, scope:string, collapse_key:string | null, alias_keys:string[]
- `Cursor` — index:int32
- `EntityState` — artifact_states:Record<unknown>, last_event_seq:int64
- `NormalizedIntent` — action:IntentAction, target_type:string, target_id:string
- `ReplayEvent` — event_id:string, causal_chain_id:string, sequence:int64, timestamp:int64, entity_key:string, artifact_id:string, state_delta:Record<unknown>
- `ResolvedArtifact` — artifact_id:string, patch:Record<unknown>
- `RuntimeState` — entities:Record<EntityState>, version:int64
- `Snapshot` — state:RuntimeState, ccnf_version:int64, collapse_version:int64, rehydration_version:int64, timestamp:int64
- `SnapshotContext` — snapshot:Snapshot, source_events:ReplayEvent[]
- `StateDelta` — artifact_id:string, before_hash:string | null, after_hash:string, patch:Record<unknown>
- `Vector` — name:string, ccnf_version:int32, invariants_tested:string[], description:string, input:Record<unknown>, expected:VectorExpected
- `VectorExpected` — entity_key:string, canonical_hash:string, cer:Cer, error:string

### ccnf-verifier (10)

- `Cer` — event_id:string, event_version:uint32, ccnf_version:uint32, system:string, domain:string, timestamp:int64, actor:Record<unknown>, intent:Record<unknown>, entity_key:string, identity_type:string, scope:string, collapse_key:string | null…
- `ComparisonResult` — vector_name:string, rust_hash:string, go_hash:string, expected_hash:string, hashes_match:boolean, go_matches_expected:boolean
- `ExpectedHash` — name:string, hash:string
- `ReplayEvent` — event_id:string, prev_event_id:string, delta:StateDeltaWrites, delta_hash:string
- `ReplayInput` — events:ReplayEvent[], cer_root_hash:string, trace_root_hash:string, replay_binding_hash:string, ccnf_version:uint32, semantics_version:uint32, event_count:uint64
- `ReplayOutput` — final_state:RuntimeState, event_count:uint64, cer_root_hash:string, trace_root_hash:string, replay_binding_hash:string
- `RuntimeState` — data:Record<StateValue>, version:uint64
- `StateDeltaWrites` — writes:Record<StateValue>
- `VectorEntry` — name:string, path:string, input:Record<unknown>
- `VerifierSummary` — total:int32, passed:int32, failed:int32, results:ComparisonResult[]

### conduit-kernel (4)

- `ConsistencyCheck` — consistent:boolean, issues:string[]
- `IdentityResponse` — identityId:string, label:string, attributes:Record<unknown>
- `SessionResponse` — sessionId:string, status:string, running:boolean, cost:float
- `StateSummary` — state:string, revision:string

### control-edge (14)

- `Health` — status:string, service:string, db:string, redis:string, timestamp:string
- `Link` — id:string, address:string, imagename:string, text:string | null, type:"link" | "separator", sortOrder:int32, createdAt:string, updatedAt:string
- `LinkCreate` — address:string, imagename:string, text:string | null, type:"link" | "separator", sortOrder:int32
- `LinkReorder` — items:ReorderItem[]
- `LinkUpdate` — address:string, imagename:string, text:string | null, type:"link" | "separator", sortOrder:int32
- `MemorySyncResult` — procedures:int32, roleIndices:int32, timestamp:string
- `Ok` — ok:boolean
- `ProcedureCard` — slug:string, title:string, summary:string, body_md:string, tags:string[], triggers:string[], mcp_tools:string[], roles:string[], updated_at:string
- `ProcedureIndexEntry` — slug:string, summary:string, tags:string[]
- `PromptCard` — id:string, role:string, slug:string, version:int32, title:string, body_md:string, parameter_schema:Record<unknown>, tags:string[], created_at:string, updated_at:string
- `PromptIndexEntry` — slug:string, title:string, version:int32, tags:string[], updated_at:string
- `PromptSyncResult` — prompts:int32, rolePromptIndices:int32, tasks:int32, roleTaskIndices:int32, timestamp:string
- `ReorderItem` — id:string, sortOrder:int32
- `TaskIndexEntry` — task_slug:string, scope:string, acceptance_criteria:string[], prompt_id:string, prompt_slug:string, updated_at:string

### draft-srv (9)

- `ColumnInfo` — name:string, type:string, isPrimaryKey:boolean, isNullable:boolean, isForeignKey:boolean, referencesTable:string, referencesColumn:string, defaultValue:string
- `ConnSpec` — engine:string, host:string, port:int32 | string, database:string, username:string, password:string, ssl:boolean
- `EngineCapabilities` — id:string, label:string, defaultPort:int32, available:boolean, missingDeps:string[], supportsDdl:boolean, supportsSchemas:boolean
- `IndexInfo` — name:string, columns:string[], isUnique:boolean
- `ProcedureInfo` — name:string, schema:string, returnType:string, parameters:{name: string, type: string}[], definition:string, comment:string
- `SchemaObject` — name:string, tables:TableInfo[], views:ViewInfo[], triggers:TriggerInfo[], procedures:ProcedureInfo[]
- `TableInfo` — name:string, schema:string, rowCount:int64, columns:ColumnInfo[], indexes:IndexInfo[], comment:string
- `TriggerInfo` — name:string, schema:string, tableName:string, timing:string, event:string, functionName:string, definition:string
- `ViewInfo` — name:string, schema:string, definition:string

### execution-srv (11)

- `ExecutionAttempt` — id:string, lease_id:string, request_id:string, executor_id:string, status:string, started_at:string, completed_at:string, result:unknown, error:string, exit_code:int32, created_at:string
- `ExecutionLease` — id:string, request_id:string, executor_id:string, status:string, ttl_seconds:int32, acquired_at:string, expires_at:string, released_at:string, created_at:string
- `ExecutionReceipt` — id:string, attempt_id:string, request_id:string, type:string, agent_role:string, summary:string, metadata:unknown, lineage_source:string, lineage_original_id:string, issued_at:string
- `ExecutionRequest` — id:string, business_key:string, title:string, intent_type:string, objective:string, inputs:unknown, deterministic:boolean, max_retries:int32, timeout_policy:string, resource_hints:string[], op_trace:unknown, status:string…
- `ExecutorHealth` — executor_id:string, active_leases:int32, completed_attempts:int32, failed_attempts:int32, last_seen:string
- `IntegrityScan` — orphan_attempts:int32, orphan_receipts:int32, leases_past_expiry:int32, missing_request_lease:int32, checked_at:string
- `LeaseLifecycle` — lease:ExecutionLease, attempts:ExecutionAttempt[]
- `PipelineOrigin` — execution:ExecutionReceipt, vision:{ id: string
- `RequestState` — request:ExecutionRequest, lease:ExecutionLease, latestAttempt:ExecutionAttempt, receipts:ExecutionReceipt[]
- `StaleLease` — id:string, request_id:string, executor_id:string, status:string, expires_at:string, age_seconds:float64
- `StatusDistribution` — status:string, count:int32

### file-system-server (4)

- `FsEntry` — name:string, path:string, type:string, size:int64
- `FsItem` — name:string, type:string
- `FsListItem` — name:string, type:string, size:int64, last_modified:float64
- `HealthDetails` — fsRootDir:string, port:int32

### fs-crawler (3)

- `CrawlRule` — ruleId:string, name:string, pattern:string, action:string
- `DuplicateStats` — groups:int32, files:int32, bytes:int64
- `Library` — libraryId:string, name:string, path:string, rules:Record<unknown>[]

### governance-envelope (14)

- `AdmissionReceipt` — peb_transaction_id:string, admission_receipt_id:string, sanctioned_transition_id:string | null, authority_result:AdmissionAuthorityResult
- `AdmissionResult` — envelope_id:string, disposition:EnvelopeDisposition, refusal_code:RefusalCode | null, evaluation_fingerprint:string, receipt:AdmissionReceipt | null
- `AssertionResult` — proposition_id:string, result:boolean, detail:string | null
- `EvaluationFingerprint` — evaluation_fingerprint:string, fingerprint_algorithm:string, fingerprint_version:int32
- `EvidenceBundle` — evidence_ids:string[], evidence_fingerprint:string | null
- `ExecutionAuthority` — lease_id:string | null, grant_id:string | null, attempt_id:string | null
- `FrameValue` — frame:string, value:unknown
- `GovernanceContract` — contract_id:string, contract_version:int32, contract_digest:string, projection_id:string | null, projection_version:int32 | null, projection_digest:string | null, operation:string, transition:string | null
- `GovernanceEnvelope` — envelope_version:int32, envelope_id:string, created_at:utcDateTime, contract:GovernanceContract, semantic:SemanticIdentity, workflow:WorkflowContext, law:LawSnapshot, execution:ExecutionAuthority | null, inputs:InputSnapshot, evaluation:GovernanceEvaluation, evidence:EvidenceBundle | null, fingerprint:EvaluationFingerprint…
- `GovernanceEvaluation` — assertion_results:AssertionResult[], disposition:EnvelopeDisposition, unknowns:string[], refusal_code:RefusalCode | null, diagnostics:string[] | null, evaluated_at:utcDateTime
- `InputSnapshot` — input_snapshot_id:string, input_captured_at:utcDateTime, input_fingerprint:string
- `LawSnapshot` — proposition_ids:string[], frame_values:FrameValue[], doctrine_ids:string[], posture_ids:string[] | null, effective_at:utcDateTime | null
- `SemanticIdentity` — subject_id:string, subject_type:string, subject_ref:string | null
- `WorkflowContext` — workflow_id:string, workflow_version:int32, node_id:string, work_request_id:string | null, work_request_version:int32 | null

### harness-srv (5)

- `ActiveSession` — jobId:string, role:string, startedAt:utcDateTime, elapsedSeconds:int32
- `OutcomeSummary` — code:string, description:string
- `ParsedOutcome` — code:string, id:string, confidence:string
- `RunEvents` — started:string
- `TaskSummary` — wind_task_id:string, wind_task_name:string, task_slug:string, scope:string

### losm-host (1)

- `SpecIR` — ir:Record<unknown>

### moleculer (2)

- `SearchInformation` — totalResults:string, searchTime:float64
- `SearchResultItem` — title:string, link:string, snippet:string

### nebula-srv (21)

- `AgentRecord` — id:string, recordType:string, role:string, title:string, content:string, sourcePath:string, agentMetadata:unknown, tags:string[], systemId:string, subsystemId:string, featureId:string, planRef:string…
- `ArchitectSpec` — id:string, title:string, body:string, systemId:string, requirementId:string, createdAt:string
- `ArtifactProvenance` — id:string, source:string, artifactType:string, artifactId:string, metadata:unknown, createdAt:string
- `Assessment` — id:string, title:string, body:string, status:string, createdAt:string
- `DocEntry` — id:string, title:string, path:string, systemId:string, subsystemId:string, createdAt:string
- `ExternalId` — id:string, systemId:string, externalSystem:string, externalId:string, label:string
- `Feature` — id:string, name:string, description:string, subsystemId:string, systemId:string, status:string, priority:string, createdAt:string, updatedAt:string
- `HarvestCandidate` — id:string, title:string, description:string, compilationReadiness:float64, systemName:string, subsystemName:string, status:string, systemId:string, subsystemId:string, createdAt:string
- `ImplementationPlan` — id:string, number:string, title:string, goal:string, status:string, createdAt:string
- `InventoryItem` — id:string, entityType:string, entityId:string, name:string, systemName:string, subsystemName:string
- `Observation` — id:string, entityId:string, entityType:string, value:unknown, createdAt:string
- `OpenQuestion` — id:string, title:string, body:string, status:string, askedBy:string, createdAt:string
- `QuestionAnswer` — id:string, body:string, answeredBy:string, createdAt:string
- `Requirement` — id:string, title:string, description:string, systemId:string, subsystemId:string, featureId:string, status:string, priority:string, candidateId:string, planRef:string, dependencies:string[], childCount:int32…
- `Role` — id:string, name:string, description:string, createdAt:string
- `RoleDrift` — id:string, name:string, expectedCount:int32, actualCount:int32, driftDirection:string
- `SemanticSearchHit` — id:string, entityType:string, title:string, snippet:string, score:float64
- `Session` — id:string, role:string, channel:string, status:string, startedAt:string, endedAt:string, createdAt:string
- `Subsystem` — id:string, name:string, description:string, systemId:string, parentId:string, createdAt:string, updatedAt:string
- `System` — id:string, name:string, description:string, kind:string, parentId:string, createdAt:string, updatedAt:string
- `Workspace` — id:string, name:string, description:string, createdAt:string

### nexus-broker (10)

- `HarnessSession` — job_id:string, role:string, started_at:string, elapsed_ms:int32, wind_task_id:string, pid:int32
- `HarnessSessions` — count:int32, sessions:HarnessSession[]
- `Health` — status:string, service:string, workers:string[], timestamp:string
- `KeychainActiveInstance` — instance_id:string, record_id:string, version:int32, role:string, supersession_type:string, asset_id:string | null
- `KeychainSnapshotRequest` — label:string, trigger:string, triggerEvent:KeychainTriggerEvent
- `KeychainTriggerEvent` — kind:string, id:string, actor:string, source:string, correlation_id:string, contract_id:string, evaluator_id:string, law_id:string, effective_at:string, meta:Record<unknown>
- `PtySession` — id:string, pid:int32, shell:string, started_at:string, status:string, exit_code:int32 | null, cols:int32, rows:int32
- `WorkerEntry` — name:string, status:string, wave:int32
- `WorkerHealth` — status:string, db:boolean, active_sessions:int32, schema:string, counts:Record<unknown>
- `Workers` — workers:WorkerEntry[], nodeID:string

### nexus-tools (26)

- `ApiDocsDriftEntry` — status:"ok" | "drift" | "missing", detail:string
- `ApiEndpoint` — method:HttpMethod, path:string, summary:string
- `ApiInventory` — service:string, endpoints:ApiEndpoint[]
- `ArlReport` — graph:GovernanceGraph
- `AuditCategoryItems` — count:int32, items:Violation[]
- `AuthorityEntry` — domain:string, canonical:string, superseded:string[], projections:string[]
- `AuthorityMatrix` — authorities:AuthorityEntry[], semantic_class_keys:Record<string[]>
- `AuthorityReport` — matrix:string
- `CapabilityNode` — id:string, input_schema:Record<unknown>, output_schema:Record<unknown>, implementation:string
- `CirLintReport` — rules_applied:CirRule[], enforcement:EnforcementLevel
- `ContextMapEntry` — url:string, path:string | null
- `ContractAuditReport` — status:"PASS" | "FAIL", categories:Record<AuditCategoryItems>, total_violations:int32, gates:GateResult[], failed_gates:string[]
- `ExternalToolInterface` — protocol:string
- `GateReport` — status:"PASS" | "FAIL", total_violations:int32, violations:Violation[]
- `GateResult` — gate:AuditGate, passed:boolean, exit_code:int32, skipped:boolean
- `GovernanceGraph` — nodes:int32, edges:int32, cycles:int32, forbidden_edges:int32
- `LayerDefinition` — domain:DomainLabel, keywords:string[], forbidden:string[]
- `PatchAction` — path:string, kind:"remove" | "downgrade" | "quarantine", rule:CirRule, applied:boolean, diff:string
- `PathClassification` — domain:DomainLabel, subtype:"CANONICAL" | "ASPIRATIONAL" | "STATEFUL", mode:"AUTHORITATIVE" | "STRUCTURAL" | "DERIVATIONAL" | "STATEFUL"
- `ProjectionIREntry` — source_operator:string, domain:string, proposition:ProjectionProposition, confidence:float64, constraints:string[], trace:string[]
- `ProjectionIRStream` — stream:ProjectionIREntry[], count:int32, valid:int32, failures:(string | null)[]
- `ProjectionProposition` — outputPath:string | null, generator:string | null, lifecycle:string | null, active:boolean
- `ScanHit` — category:ScanCategory, path:string, line:int32, pattern:string
- `Violation` — failure_class:string, violation_type:string, rule:string, code:string, domain:string, path:string, location:string, detail:string, description:string, severity:Severity
- `Workflow` — id:string, nodes:CapabilityNode[], edges:WorkflowEdge[], entry_point:string, output_node:string
- `WorkflowEdge` — source:string, target:string

### peb-kernel (18)

- `AppendTraceSegmentInput` — entity_id:string, work_request_id:string, parent_trace_id:string, stage:string, inputs:unknown, causal_entries:unknown, rejected_alternatives:unknown, confidence:float32
- `CapabilityToken` — value:string
- `CheckInvariantsInput` — entity_id:string, proposed_action:unknown
- `ExtensionProposalInput` — entity_id:string, gap_description:string, proposed_content:unknown, target_key:string, rationale:string
- `MalformedAdmissionError` — message:string
- `PebAdmissionResult` — transaction_id:string, envelope_id:string, evaluation_fingerprint:string, admission_result:AdmissionResult, message:string, admitted:boolean
- `PebCapability` — id:string, entityId:string, capability:string, grantedBy:string, expiresAt:utcDateTime, createdAt:utcDateTime, active:boolean
- `PebDecision` — id:string, transactionId:string, adrNumber:string, title:string, status:DecisionStatus, summary:unknown, affectedKeys:string[], entropyClass:EntropyClass, beforeHash:string, afterHash:string, authorId:string, parentDecisionId:string…
- `PebState` — id:string, key:string, content:unknown, metadata:unknown, checksum:string, version:int64, createdAt:utcDateTime, updatedAt:utcDateTime
- `PebStateHash` — value:string
- `PebTrace` — id:string, transactionId:string, workRequestId:string, parentTraceId:string, stage:string, inputs:unknown, causalEntries:unknown, rejectedAlternatives:unknown, confidence:float32, status:string, createdAt:utcDateTime
- `PebTransaction` — id:string, envelope_id:string, evaluation_fingerprint:string, contract_digest:string, idempotencyKey:string, entityId:string, admissionResult:AdmissionResult, toolName:string, input:unknown, output:unknown, beforeHash:string, afterHash:string…
- `PebViolation` — id:string, transactionId:string, violationType:ViolationType, severity:ViolationSeverity, entityId:string, capabilityAttempted:string, context:unknown, resolution:ViolationResolution, createdAt:utcDateTime
- `RecordDecisionInput` — entity_id:string, title:string, summary:unknown, affected_keys:string[], entropy_class:EntropyClass, commit_ref:string
- `ReportViolationInput` — entity_id:string, violation_type:string, severity:string, context:unknown, capability_attempted:string
- `RequestClarificationInput` — entity_id:string, work_request_id:string, ambiguity:string, options_considered:unknown, proposed_resolution:string
- `ValidateTransformInput` — entity_id:string, state_view:unknown, context:unknown, proposed_delta:unknown, work_request_id:string
- `ValidateTransitionInput` — entity_id:string, from_state:string, to_state:string

### service-broker (55)

- `AbstractContent` — id:string, created:utcDateTime, updated:utcDateTime, text:string, url:string, rating:int64
- `AbstractContentDTO` — id:string, postedBy:string, postedTo:string, postedDate:string, text:string, rating:int64, url:string
- `AdminLogEntry` — id:string, timestamp:utcDateTime, serverId:string, serverPort:int32, serverConfig:string, userId:string, service:string, operation:string, requestParams:string, successStatus:boolean, errorMessage:string, requestId:string
- `BrokerConfig` — basePath:string, enableLogging:boolean, defaultTimeout:int32
- `BrokerOperation` — name:string, params:BrokerParam[]
- `BrokerParam` — key:string, value:string
- `BrokerTrafficEvent` — eventId:string, timestamp:string, durationMs:int64, requestId:string, service:string, operation:string, ok:boolean, httpStatus:int32, source:string, request:ServiceRequest, response:ServiceResponse<unknown>, errorMessage:string
- `Column` — id:int64, name:string, table:Table, index:int32
- `Comment` — postedBy:User, post:Post, replies:Comment[], reactions:Reaction[], edits:Edit[]
- `CommentDTO` — postId:string, parentId:string
- `DBDataSource` — id:int64, name:string, driver:string, url:string, username:string, passwordRef:string
- `DBExport` — id:int64, name:string, description:string, source:ExportSource, pageSize:PdfPageSize = PdfPageSize.A4, pageOrientation:string = "portrait", dataSource:DBDataSource, fields:DBField[], createdAt:utcDateTime, updatedAt:utcDateTime, enabled:boolean = true
- `DBField` — id:int64, name:string, sourceColumn:string, displayName:string, position:int32, required:boolean = false, fieldType:DBFieldType, style:Style, exportId:int64
- `DBFieldType` — id:int64, name:FieldTypeEnum, displayPattern:string, formatMask:string
- `Edit` — id:string, created:utcDateTime, updated:utcDateTime, text:string, post:Post, comment:Comment
- `Forum` — id:string, name:string
- `ForumDTO` — id:string, name:string
- `FsItem` — name:string, type:string, size:int64, lastModified:utcDateTime, lastModifiedDate:string, url:string, thumbnailUrl:string, deleteUrl:string, deleteType:string
- `FsItemReference` — name:string, type:"file" | "folder"
- `Interest` — id:string, name:string
- `Join` — id:int64, joinColumnA:Column, joinColumnB:Column, joinType:JoinType
- `JoinType` — code:int32, name:string
- `LogError` — lineNumber:int32, line:string
- `LongValue` — id:int64, value:int64
- `Note` — id:string, userId:string, source:string, key:string, content:string
- `Post` — postedBy:User, postedTo:User, forumId:int64, sourceUrl:string, title:string, replies:Comment[], edits:Edit[], reactions:Reaction[]
- `PostDTO` — forumId:int64
- `PostStatDTO` — id:int64, rating:int64, postId:int64
- `Profile` — id:string, firstName:string, lastName:string, city:string, state:string, profileImageUrl:string, user:User, interests:Interest[]
- `ProfileDTO` — id:string, firstName:string, lastName:string, city:string, state:string, profileImageUrl:string
- `Query` — id:int64, name:string, schema:string
- `Reaction` — id:string, created:utcDateTime, reactionType:ReactionType, user:User, post:Post, comment:Comment
- `ReactionDTO` — id:string, type:string
- `ResponseError` — field:string, message:string
- `SearchResultItem` — kind:string, title:string, htmlTitle:string, link:string, displayLink:string, snippet:string, htmlSnippet:string, formattedUrl:string, htmlFormattedUrl:string, pagemap:Record<unknown>, metatags:Record<string>[], cseThumbnail:Record<string>[]…
- `SearchResultsCacheEntry` — id:string, query:string, items:SearchResultItem[], timestamp:utcDateTime, expiresAt:utcDateTime
- `ServiceRegistration` — serviceName:string, operations:string[], endpoint:string, healthCheck:string, metadata:Record<unknown>, lastHeartbeat:utcDateTime, status:ServiceStatus
- `ServiceResponseBody` — ok:boolean, data:unknown, errors:ResponseError[], requestId:string, ts:utcDateTime, version:string, service:string, operation:string, encrypt:boolean
- `StringValue` — id:int64, value:string
- `Style` — id:int64, name:string, styleType:StyleType
- `StyleType` — id:int64, name:StyleTypeEnum, fontName:string, fontSize:int32, bold:boolean = false, italic:boolean = false, foreground:string, background:string
- `Table` — id:int64, name:string, schema:string
- `User` — id:string, identifier:string, email:string
- `UserDTO` — id:string, identifier:string, email:string, avatarUrl:string, admin:boolean
- `UserRegistration` — id:int64, userAlias:string, email:string, admin:boolean = false, createdAt:utcDateTime, updatedAt:utcDateTime
- `UserRegistrationDTO` — id:string, identifier:string, email:string, avatarUrl:string, admin:boolean
- `Value` — id:int64
- `ValueType` — code:int32, tableName:string

### service-registry (27)

- `Deployment` — id:int64, service:Service, environment:EnvironmentType, server:Server, version:string, deployedAt:utcDateTime, status:string, port:int32, contextPath:string, healthCheckUrl:string, healthStatus:string, lastHealthCheck:utcDateTime…
- `DescribedLookup` — description:string
- `EnvironmentType` — id:int64, name:string, description:string, activeFlag:boolean = true, createdAt:utcDateTime, updatedAt:utcDateTime
- `ExternalServiceRegistration` — serviceName:string, operations:string[], endpoint:string, healthCheck:string, metadata:Record<unknown>, framework:string, version:string, port:int32, dependencies:string[], hostedServices:HostedServiceInfo[], serverId:int64, hostname:string
- `Framework` — id:int64, name:string, description:string, vendor:FrameworkVendor, category:FrameworkType, language:FrameworkLanguage, currentVersion:string, ltsVersion:string, url:string, supportsBrokerPattern:boolean = false, activeFlag:boolean = true, createdAt:utcDateTime…
- `FrameworkLanguage` — id:int64, name:string, description:string, url:string, currentVersion:string, ltsVersion:string, activeFlag:boolean = true
- `FrameworkLanguageCreate` — name:string, description:string, url:string, currentVersion:string, ltsVersion:string, activeFlag:boolean
- `FrameworkLanguageUpdate` — name:string, description:string, url:string, currentVersion:string, ltsVersion:string, activeFlag:boolean
- `FrameworkType` — id:int64, name:string, description:string, activeFlag:boolean = true, createdAt:utcDateTime, updatedAt:utcDateTime
- `FrameworkTypeCreate` — name:string, description:string, activeFlag:boolean = true
- `FrameworkTypeUpdate` — id:int64, name:string, description:string, activeFlag:boolean
- `FrameworkVendor` — id:int64, name:string, description:string, url:string, activeFlag:boolean = true
- `FrameworkVendorCreate` — name:string, description:string, url:string, activeFlag:boolean = true
- `FrameworkVendorUpdate` — id:int64, name:string, description:string, url:string, activeFlag:boolean
- `Library` — id:int64, name:string, description:string, category:LibraryType, language:FrameworkLanguage, currentVersion:string, packageName:string, packageManager:string, url:string, repositoryUrl:string, license:string, activeFlag:boolean = true…
- `LibraryType` — id:int64, name:string, description:string, activeFlag:boolean = true, createdAt:utcDateTime, updatedAt:utcDateTime
- `LinkedLookup` — url:string
- `LookupBase` — id:int64, name:string, activeFlag:boolean = true
- `OperatingSystem` — id:int64, name:string, description:string, activeFlag:boolean = true
- `Server` — id:int64, hostname:string, ipAddress:string, type:ServerType, environmentType:EnvironmentType, operatingSystem:OperatingSystem, cpuCores:int32, memory:string, disk:string, status:string, region:string, cloudProvider:string…
- `ServerType` — id:int64, name:string, description:string, activeFlag:boolean = true, createdAt:utcDateTime, updatedAt:utcDateTime
- `Service` — id:int64, name:string, description:string, framework:Framework, type:ServiceType, componentOverride:VisualComponent, parentService:Service, defaultPort:int32, apiBasePath:string, repositoryUrl:string, version:string, status:string…
- `ServiceBackend` — id:int64, name:string, activeFlag:boolean = true
- `ServiceConfiguration` — id:int64, key:string, value:string
- `ServiceDependency` — id:int64, name:string
- `ServiceType` — id:int64, name:string, description:string, activeFlag:boolean = true, defaultComponent:VisualComponent, createdAt:utcDateTime, updatedAt:utcDateTime
- `VisualComponent` — id:int64, name:string

### substance (3)

- `DomainLinkIn` — segmentSetId:string, role:string
- `DomainLinkOut` — segmentSetId:string, role:string, active:boolean, segmentSet:SegmentSetOut
- `SegmentSetOut` — segmentSetId:string, name:string, members:string[]

### tackle-registry (7)

- `ConfigBundle` — id:string, name:string, role:string, providerId:string, harnessId:string, priority:int32, invocationMode:string, command:string, endpointUrl:string, timeoutMs:int32, isActive:boolean
- `Harness` — id:string, name:string, invocationSemantics:Record<unknown>
- `ModelRow` — id:string, name:string, harnessId:string, providerId:string, verified:boolean
- `Provider` — id:string, name:string, type:string, endpointUrl:string, apiKey:string, configJson:Record<unknown>
- `ResolvedFallback` — priority:int32, providerType:string, providerName:string
- `ResolvedRoleConfig` — role:string, providerId:string, providerName:string, providerType:string, apiKey:string, endpointUrl:string, harnessName:string, fallbacks:ResolvedFallback[]
- `RoleRow` — id:string, name:string, description:string

### terrain (26)

- `BrokerProfile` — id:int64, profileId:string, name:string, brokerUrl:string, imageUrl:string, autoConnect:boolean = false, healthCheckDelayMinutes:int32
- `BrokerProfileCreate` — profileId:string, name:string, brokerUrl:string, imageUrl:string, autoConnect:boolean = false, healthCheckDelayMinutes:int32
- `BrokerProfileUpdate` — profileId:string, name:string, brokerUrl:string, imageUrl:string, autoConnect:boolean, healthCheckDelayMinutes:int32
- `CliTool` — id:int64, name:string, toolPath:string, description:string, invocation:string, language:string, category:string, startup:string, startupScript:string, buildCommand:string, health:string, sysUser:string…
- `CliToolCreate` — name:string, toolPath:string, description:string, invocation:string, language:string, category:string, startup:string, startupScript:string, buildCommand:string, health:string, sysUser:string, sysPass:string…
- `CliToolUpdate` — name:string, toolPath:string, description:string, invocation:string, language:string, category:string, startup:string, startupScript:string, buildCommand:string, health:string, sysUser:string, sysPass:string…
- `LookupNotFound` — unit:string, error:string
- `McpServer` — id:int64, name:string, port:int32, workspacePath:string, serviceTypeId:int64, serviceType:ServiceType, healthCheckUrl:string, status:string, transportType:string, version:string, description:string, repositoryUrl:string…
- `McpServerCreate` — name:string, port:int32, workspacePath:string, serviceTypeId:int64, healthCheckUrl:string, status:string, transportType:string, version:string, description:string, repositoryUrl:string, activeFlag:boolean = true, startup:string…
- `McpServerUpdate` — name:string, port:int32, workspacePath:string, serviceTypeId:int64, healthCheckUrl:string, status:string, transportType:string, version:string, description:string, repositoryUrl:string, activeFlag:boolean, startup:string…
- `RegistryServerProfile` — id:int64, profileId:string, name:string, registryServerUrl:string, imageUrl:string, isActive:boolean = false, description:string
- `RegistryServerProfileCreate` — profileId:string, name:string, registryServerUrl:string, imageUrl:string, isActive:boolean = false, description:string
- `RegistryServerProfileUpdate` — profileId:string, name:string, registryServerUrl:string, imageUrl:string, isActive:boolean, description:string
- `RunnableService` — id:int64, name:string, port:int32, workspacePath:string, serviceTypeId:int64, serviceType:ServiceType, healthCheckUrl:string, status:string, version:string, description:string, repositoryUrl:string, activeFlag:boolean = true…
- `RunnableServiceCreate` — name:string, port:int32, workspacePath:string, serviceTypeId:int64, healthCheckUrl:string, status:string, version:string, description:string, repositoryUrl:string, activeFlag:boolean = true, startup:string, startupScript:string…
- `RunnableServiceUpdate` — name:string, port:int32, workspacePath:string, serviceTypeId:int64, healthCheckUrl:string, status:string, version:string, description:string, repositoryUrl:string, activeFlag:boolean, startup:string, startupScript:string…
- `Server` — id:int64, hostname:string, ipAddress:string, os:string, status:string, activeFlag:boolean = true, startup:string, startupScript:string, buildCommand:string, health:string, sysUser:string, sysPass:string…
- `ServerCreate` — hostname:string, ipAddress:string, os:string, status:string, activeFlag:boolean = true, startup:string, startupScript:string, buildCommand:string, health:string, sysUser:string, sysPass:string, notes:string…
- `ServerUpdate` — hostname:string, ipAddress:string, os:string, status:string, activeFlag:boolean, startup:string, startupScript:string, buildCommand:string, health:string, sysUser:string, sysPass:string, notes:string…
- `ServiceDependency` — id:int64, sourceType:string, sourceId:int64, targetType:string, targetId:int64, criticality:string, description:string
- `ServiceDependencyCreate` — sourceType:string, sourceId:int64, targetType:string, targetId:int64, criticality:string, description:string
- `ServiceDependencyUpdate` — sourceType:string, sourceId:int64, targetType:string, targetId:int64, criticality:string, description:string
- `ServiceEndpoint` — id:string, unit:string, instance:string = "primary", host:string, ip:string, port:int32, scheme:string = "http", status:string = "UNKNOWN", lastHeartbeat:utcDateTime
- `ServiceType` — id:int64, name:string
- `ServiceTypeCreate` — name:string
- `ServiceTypeUpdate` — name:string

### timeclock (1)

- `TimeclockStats` — activeSessions:int32, totalSessions:int32

### tools-aggregator (6)

- `HealthServices` — total:int32, reachable:int32, status:Record<unknown>
- `HealthTools` — total:int32
- `InitRegistrySummary` — totalTools:int32, services:Record<unknown>, toolsByService:Record<int32>
- `InputSchema` — type:string, properties:Record<unknown>, required:string[]
- `ToolDetail` — name:string, description:string, service:string, serviceUrl:string, inputSchema:InputSchema, protocol:MCPProtocol
- `ToolEntry` — name:string, description:string, service:string, inputSchema:InputSchema, protocol:MCPProtocol

### ui-event-bus (1)

- `UiEvent` — sender:string, eventName:string, eventValue:unknown

### ui-tools (2)

- `Link` — id:string, address:string, imagename:string, text:string | null, type:LinkType, sortOrder:int32, createdAt:utcDateTime, updatedAt:utcDateTime
- `ReorderItem` — id:string, sortOrder:int32

### wind-srv (10)

- `Edge` — id:string, sourceEventId:string, targetNodeId:string, condition:string
- `EventType` — eventType:string, label:string, createdAt:string
- `Office` — id:string, name:string, description:string
- `RoleDef` — id:string, name:string, officeId:string, titleId:string
- `Ticket` — id:string, nodeId:string, instanceId:string, status:string, assignedTo:string, createdAt:string, updatedAt:string
- `Title` — id:string, name:string, officeId:string
- `Version` — id:string, workflowId:string, version:string, status:string, createdAt:string
- `Workflow` — id:string, title:string, version:string, description:string, createdAt:string
- `WorkflowInstance` — id:string, workflowId:string, status:string, currentNodeId:string, startedAt:string, completedAt:string, createdAt:string
- `WorkflowNode` — id:string, workflowId:string, name:string, officeId:string, eventType:string, nextNodeId:string

## Cross-service identity collisions (explicit findings)

- **Forum** (2x, DOMAIN): assembly-srv, service-broker
- **ErrorDetail** (6x, ENVELOPE): assembly-srv, cascade-srv, execution-srv, file-system-server, nebula-srv, wind-srv
- **HealthResponse** (10x, ENVELOPE): assembly-srv, cascade-srv, execution-srv, file-system-server, harness-srv, nebula-srv, tools-aggregator, ui-event-bus, ui-tools, wind-srv
- **Cer** (2x, DOMAIN): ccnf-ref, ccnf-verifier
- **ReplayEvent** (2x, DOMAIN): ccnf-ref, ccnf-verifier
- **RuntimeState** (2x, DOMAIN): ccnf-ref, ccnf-verifier
- **Health** (2x, DOMAIN): control-edge, nexus-broker
- **Link** (2x, DOMAIN): control-edge, ui-tools
- **ReorderItem** (2x, DOMAIN): control-edge, ui-tools
- **ErrorResponse** (5x, ENVELOPE): control-edge, tackle-registry, tools-aggregator, ui-event-bus, ui-tools
- **FsItem** (3x, DOMAIN): file-system-server, service-broker
- **FsRequest** (3x, ENVELOPE): file-system-server, service-broker
- **Library** (2x, DOMAIN): fs-crawler, service-registry
- **WorkRequestResponse** (2x, ENVELOPE): losm-host, vision-srv
- **BranchResponse** (2x, ENVELOPE): losm-host, vision-srv
- **SearchResultItem** (2x, DOMAIN): moleculer, service-broker
- **Workflow** (2x, DOMAIN): nexus-tools, wind-srv
- **PebTransactionRequest** (2x, ENVELOPE): peb-kernel
- **AdmissionResponse** (2x, ENVELOPE): peb-kernel
- **PebHealthResponse** (2x, ENVELOPE): peb-kernel
- **BrokerTrafficEvent** (2x, DOMAIN): service-broker
- **DBDataSource** (2x, DOMAIN): service-broker
- **DBFieldType** (2x, DOMAIN): service-broker
- **DBField** (2x, DOMAIN): service-broker
- **FsListResponse** (2x, ENVELOPE): service-broker
- **FsItemReference** (2x, DOMAIN): service-broker
- **FsOperationResponse** (2x, ENVELOPE): service-broker
- **User** (2x, DOMAIN): service-broker
- **ServiceType** (2x, DOMAIN): service-registry, terrain
- **Server** (2x, DOMAIN): service-registry, terrain
- **ServiceDependency** (2x, DOMAIN): service-registry, terrain
