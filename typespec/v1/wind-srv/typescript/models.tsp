/**
 * wind-srv — TypeSpec Models
 */
namespace org.nexus.windsrv {

  model Ticket {
    id: string;
    nodeId: string;
    instanceId: string;
    status: string;
    assignedTo?: string;
    /** Lease-bound assignment — supersedes `assignedTo` as it populates.
     *  Liveness comes free: tickets whose lease died re-offer. */
    assignedLease?: LeaseBinding;
    createdAt?: string;
    updatedAt?: string;
  }

  model WorkflowInstance {
    id: string;
    workflowId: string;
    status: string;
    currentNodeId?: string;
    startedAt?: string;
    completedAt?: string;
    createdAt?: string;
  }

  model Workflow {
    id: string;
    title: string;
    version?: string;
    description?: string;
    createdAt?: string;
  }

  model WorkflowNode {
    id: string;
    workflowId: string;
    name: string;
    officeId?: string;
    eventType: string;
    nextNodeId?: string;
    /** Capability requirements the ACTIVE role must satisfy at node entry.
     *  Nodes declare what the WORK REQUIRES; the registry resolves which
     *  live role lawfully holds it. `officeId` stays human-organizational
     *  (bind, never collapse). Staged inert: warn-mode resolution only
     *  until the operator enforce flip. */
    requirements?: NodeRequirement[];
  }

  // ── Capability-based node requirements (staged inert) ────────────────
  //
  // Mirrors the live registry vocabulary exactly:
  //   - V172 nebula.capabilities  (demand-side atom, provider-agnostic)
  //   - V172 nebula.adapters      (protocol x provider x status)
  //   - V174 satisfaction states  (satisfied / satisfied-stale /
  //     unsatisfied / unreachable / refused / unknown — never collapse
  //     non-satisfied cases into one value)
  //   - V175 roles_history        (open-snapshot capability credentials)

  /** V174 satisfaction verdict — mirrored from sql/V174__satisfaction_states.sql. */
  union SatisfactionVerdict {
    string;
    "satisfied";
    "satisfied-stale";
    "unsatisfied";
    "unreachable";
    "refused";
    "unknown";
  }

  /** Credential atom a role must hold on its live roles_history snapshot.
   *  Maps to the roles_history capability columns + domain ownership. */
  union RoleCredential {
    string;
    "can_verify_work_requests";
    "can_greenlight";
    "can_create_questions";
    "can_create_agendas";
    "can_resolve_questions";
    "owns_domain";
  }

  /** One required capability on a node: the capability names the contract;
   *  satisfaction runs through adapters and the V174 verdict — the same
   *  measure/demand/satisfy triangle the capability registry implements. */
  model NodeRequirement {
    id: string;
    nodeId: string;
    /** V172 nebula.capabilities key — e.g. "has-active-shrapnel-protocol". */
    capabilityKey: string;
    /** Minimum credential the bound role must hold (V175 column or domain). */
    roleCredential: RoleCredential;
    /** V174 verdict at last resolution — informational until enforce. */
    lastVerdict?: SatisfactionVerdict;
    lastResolvedAt?: string;
    /** Resolve-time evidence: adapter ids / probe refs behind the verdict.
     *  References only — never embedded blobs. */
    evidenceRefs?: string[];
    createdAt?: string;
  }

  /** The role a node entry resolved to, with its optional lease binding. */
  model CapabilityBinding {
    id: string;
    requirementId: string;
    /** Live role name from nebula.roles_history (open snapshot). */
    role: string;
    /** V174 verdict the binding was made under. */
    verdict: SatisfactionVerdict;
    /** Per-instance execution binding (multi-instance roles exist:
    *   engineer-ii). Role-level bindings precede lease binding. */
    leaseRef?: string;
    sessionId?: string;
    boundAt?: string;
    expiresAt?: string;
    evidenceRefs?: string[];
  }

  /** Lease-bound ticket assignment (role + session + lease_ref, per the
   *  operator-lease issuance/renewal machinery). */
  model LeaseBinding {
    role: string;
    sessionId: string;
    leaseRef: string;
    issuedAt?: string;
    expiresAt?: string;
    /** Boot-shim liveness verdict at last check (live/expired/dead). */
    liveness?: string;
  }

  /** Node-entry context payload — RESOLVED REFERENCES ONLY. Canonical
   *  surfaces (tackle registry, continuity digests, V169 connection
   *  records, work requests) remain the sole sources of truth; a bundle
   *  that embedded content would drift exactly like the mirror the
   *  V176 cutover retired. */
  model ResolvedContextBundle {
    nodeId: string;
    requirementIds: string[];
    binding?: CapabilityBinding;
    /** Procedure cards the role loads at entry (tackle.memory slugs). */
    procedureCardSlugs: string[];
    /** Continuity digest reference (assembler output id, lease-bound). */
    digestRef?: string;
    /** V169 agent connection record for the adopting session. */
    connectionRecordRef?: string;
    /** Work-surface refs the node operates on (candidates, WRs, PRs). */
    workRefs: string[];
    /** Human-organizational staging, when known. Bind, never collapse. */
    officeId?: string;
    resolvedAt?: string;
  }

  model EventType {
    eventType: string;
    label?: string;
    createdAt?: string;
  }

  model Edge {
    id: string;
    sourceEventId: string;
    targetNodeId: string;
    condition?: string;
  }

  model Office {
    id: string;
    name: string;
    description?: string;
  }

  model Title {
    id: string;
    name: string;
    officeId?: string;
  }

  model Version {
    id: string;
    workflowId: string;
    version: string;
    status?: string;
    createdAt?: string;
  }

  model RoleDef {
    id: string;
    name: string;
    officeId?: string;
    titleId?: string;
  }

  model PollRequest {
    since?: string;
    limit?: int32;
  }

  model ErrorDetail { error: string; }
  model HealthResponse { status: string; timestamp: string; }
}