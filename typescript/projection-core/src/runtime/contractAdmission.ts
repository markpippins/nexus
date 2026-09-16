/**
 * W4.06 — Contract admission hardening (fail-closed promotion gate).
 *
 * A single admission surface for contract artifact sets ahead of the
 * Wave 4 promotion ladder (advisory → narrowly blocking). Admission is
 * fail-closed: an artifact set is admitted only when
 *
 *   1. every required artifact (typespec / jsonld / cue) carries a valid
 *      sha256 digest,
 *   2. every required framing dimension is present with a non-empty value,
 *   3. the contract version is strictly greater than any previously
 *      admitted version (monotonicity), and
 *   4. a digest is never rewritten at the same version (immutability).
 *
 * Resolution grammar ownership is untouched — this module only validates
 * artifact identity and framing completeness; it never parses or
 * rewrites grammar content.
 */

export type ContractArtifactKind = "typespec" | "jsonld" | "cue";

export interface ContractArtifact {
  kind: ContractArtifactKind;
  digest: string;
}

export interface ContractAdmissionRequest {
  contractId: string;
  version: number;
  artifacts: ContractArtifact[];
  framingDimensions: Record<string, string>;
}

export interface ContractAdmissionResult {
  status: "admitted" | "refused";
  reason?: string;
  contractId?: string;
  version?: number;
}

export const REQUIRED_ARTIFACT_KINDS: readonly ContractArtifactKind[] = ["typespec", "jsonld", "cue"];

/** G1 activation (verdict 986ec482): the first narrowly-binding decision
 *  class, enforced only at this boundary via admitGoverned(). */
export const BINDING_DECISION_CLASS = "deny_contract_promotion";

export function isValidSha256Digest(value: unknown): value is string {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}

/**
 * G1 activation — the read-only binding-authority consult the host may
 * attach to the registry. See bindingAuthorityLookup.ts for the
 * PG-backed implementation.
 */
export interface BindingAuthorityConsult {
  consult: (decisionClass: string, subjectId: string) => Promise<{
    authority_level: "advisory" | "narrowly_binding";
    denying: boolean;
    latest_disposition: string | null;
    reason: string;
  }>;
}

/** Shape returned by admitGoverned: the structural verdict plus the
 *  authority provenance of the decision. */
export type GovernedAdmissionResult = ContractAdmissionResult & {
  authority_level: "advisory" | "narrowly_binding";
  decision_class: string;
  subject_id: string;
  consult_reason: string;
};

export class ContractAdmissionRegistry {
  /** contractId → highest admitted version */
  private readonly versions = new Map<string, number>();
  /** `${contractId}@${version}` → digest by kind (immutability check) */
  private readonly digests = new Map<string, Map<ContractArtifactKind, string>>();
  /**
   * G1 activation — optional binding-authority consult. When supplied by
   * the host, admitGoverned() enforces persisted PEB denials for the
   * narrowly-binding class (verdict 986ec482). The verified admission
   * predicates are unchanged; A4 still holds: the registry exposes no
   * method that flips global blocking (the consult is read-only and
   * injected; authority lives in peb.state, not in process state).
   */
  private bindingConsult?: BindingAuthorityConsult | undefined;

  constructor(private readonly requiredDimensions: readonly string[]) {
    if (!requiredDimensions || requiredDimensions.length === 0) {
      throw new Error("admission_requires_framing_dimensions");
    }
  }

  /**
   * G1 activation — attach the read-only authority consult. Passing null
   * detaches (e.g. drill harnesses that must run pre-activation
   * semantics). When the host never attaches, the boundary behaves
   * exactly as before (advisory everywhere).
   */
  setBindingAuthorityConsult(consult: BindingAuthorityConsult | null): void {
    this.bindingConsult = consult ?? undefined;
  }

  /**
   * Governed admission — the G1 activation submit surface. Runs the
   * verified structural predicates 1–5 first (unchanged semantics), then
   * consults binding authority for the subject:
   *   - advisory  -> structural verdict stands (advisory rung unchanged)
   *   - binding + persisted negative disposition -> refused with a named
   *     reason, nothing admitted anew (fail-closed, verdict 986ec482)
   *   - binding + allow/absent -> structural verdict stands (absence of a
   *     decision is not a denial; no broad peb.decisions activation)
   *   - consult error -> advisory for this call (a broken consult never
   *     widens authority)
   */
  async admitGoverned(request: ContractAdmissionRequest, subjectId: string): Promise<GovernedAdmissionResult> {
    const structural = this.admit(request);
    if (structural.status === "refused") {
      return { ...structural, authority_level: "advisory", decision_class: BINDING_DECISION_CLASS, subject_id: subjectId, consult_reason: "structural_refusal" };
    }
    if (!this.bindingConsult) {
      return { ...structural, authority_level: "advisory", decision_class: BINDING_DECISION_CLASS, subject_id: subjectId, consult_reason: "no_consult_attached" };
    }
    try {
      const verdict = await this.bindingConsult.consult(BINDING_DECISION_CLASS, subjectId);
      if (verdict.authority_level === "narrowly_binding" && verdict.denying) {
        return {
          status: "refused",
          reason: `binding_deny:${verdict.latest_disposition}`,
          authority_level: "narrowly_binding",
          decision_class: BINDING_DECISION_CLASS,
          subject_id: subjectId,
          consult_reason: verdict.reason,
        };
      }
      return {
        ...structural,
        authority_level: verdict.authority_level,
        decision_class: BINDING_DECISION_CLASS,
        subject_id: subjectId,
        consult_reason: verdict.reason,
      };
    } catch (err) {
      return {
        ...structural,
        authority_level: "advisory",
        decision_class: BINDING_DECISION_CLASS,
        subject_id: subjectId,
        consult_reason: `consult_error:${err instanceof Error ? err.message : String(err)}`,
      };
    }
  }

  admit(request: ContractAdmissionRequest): ContractAdmissionResult {
    // 1. Identity present.
    if (!request.contractId || !Number.isInteger(request.version) || request.version < 1) {
      return { status: "refused", reason: "invalid_contract_identity" };
    }
    // 2. Every required artifact kind present with a valid digest.
    const byKind = new Map<ContractArtifactKind, ContractArtifact>();
    for (const artifact of request.artifacts) {
      if (byKind.has(artifact.kind)) {
        return { status: "refused", reason: `duplicate_artifact:${artifact.kind}` };
      }
      byKind.set(artifact.kind, artifact);
    }
    for (const kind of REQUIRED_ARTIFACT_KINDS) {
      const artifact = byKind.get(kind);
      if (!artifact) {
        return { status: "refused", reason: `missing_artifact:${kind}` };
      }
      if (!isValidSha256Digest(artifact.digest)) {
        return { status: "refused", reason: `invalid_digest:${kind}` };
      }
    }
    // 3. Required framing dimensions present and non-empty.
    for (const dimension of this.requiredDimensions) {
      const value = request.framingDimensions[dimension];
      if (typeof value !== "string" || value.trim() === "") {
        return { status: "refused", reason: `missing_framing_dimension:${dimension}` };
      }
    }
    // 4. Version monotonicity.
    const prior = this.versions.get(request.contractId);
    if (prior !== undefined && request.version <= prior) {
      return { status: "refused", reason: `version_not_monotonic:${request.version}<=${prior}` };
    }
    // 5. Digest immutability at a given version.
    const versionKey = `${request.contractId}@${request.version}`;
    const priorDigests = this.digests.get(versionKey);
    if (priorDigests) {
      for (const kind of REQUIRED_ARTIFACT_KINDS) {
        const priorDigest = priorDigests.get(kind);
        if (priorDigest && priorDigest !== byKind.get(kind)!.digest) {
          return { status: "refused", reason: `digest_rewrite:${kind}@v${request.version}` };
        }
      }
    }
    // Commit.
    this.versions.set(request.contractId, request.version);
    this.digests.set(
      versionKey,
      new Map(REQUIRED_ARTIFACT_KINDS.map((kind) => [kind, byKind.get(kind)!.digest] as const)),
    );
    return { status: "admitted", contractId: request.contractId, version: request.version };
  }
}
