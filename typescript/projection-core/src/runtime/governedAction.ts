import { GovernedAdapterError } from "../adapter/governed";
import { ViewSpecAction } from "../types/viewSpec";
import { RuntimeEvent, RuntimeView } from "./types";

export type GovernedResultStatus = "admitted" | "refused" | "unknown" | "stale" | "drift" | "error";

export interface GovernedActionContext {
  candidateId: string;
  contractId: string;
  contractVersion: number;
  contractDigest: `sha256:${string}`;
  workflowId: string;
  nodeId: string;
  inputSnapshotId: string;
  action: ViewSpecAction;
}

export interface AdmissionEnvelopeRequest {
  candidateId: string;
  context: GovernedActionContext;
  envelope: Record<string, unknown>;
}

export interface AdmissionAssessmentResult {
  status: GovernedResultStatus;
  admitted: boolean;
  reason: string;
  envelopeId?: string;
  evaluationFingerprint?: `sha256:${string}`;
  admissionReceiptId?: string;
  pebTransactionId?: string;
  replayFixtureId?: string;
}

export interface GovernedAdmissionClient {
  assess(request: AdmissionEnvelopeRequest, signal?: AbortSignal): Promise<AdmissionAssessmentResult>;
}

export interface GovernedActionResult {
  action: ViewSpecAction;
  assessment: AdmissionAssessmentResult;
  pebAuthorityInvoked: boolean;
}

export class GovernedActionError extends Error {
  constructor(message: string, public readonly code: string) {
    super(message);
    this.name = "GovernedActionError";
  }
}

function validDigest(value: unknown): value is `sha256:${string}` {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}

function isAssessmentResult(value: unknown): value is AdmissionAssessmentResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<AdmissionAssessmentResult>;
  return typeof result.status === "string"
    && ["admitted", "refused", "unknown", "stale", "drift", "error"].includes(result.status)
    && typeof result.admitted === "boolean"
    && typeof result.reason === "string"
    && (result.evaluationFingerprint === undefined || validDigest(result.evaluationFingerprint));
}

export class FetchGovernedAdmissionClient implements GovernedAdmissionClient {
  constructor(private readonly endpoint: string, private readonly fetcher: typeof fetch = fetch) {}

  async assess(request: AdmissionEnvelopeRequest, signal?: AbortSignal): Promise<AdmissionAssessmentResult> {
    if (!/^https?:\/\//.test(this.endpoint)) {
      throw new GovernedActionError("Admission endpoint must be absolute HTTP(S)", "INVALID_ADMISSION_ENDPOINT");
    }
    const response = await this.fetcher(this.endpoint, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
    if (!response.ok) {
      throw new GovernedActionError(`Admission assessment failed: HTTP ${response.status}`, "ASSESSMENT_REQUEST_FAILED");
    }
    const result: unknown = await response.json();
    if (!isAssessmentResult(result)) {
      throw new GovernedActionError("Admission response is not a valid assessment", "INVALID_ASSESSMENT_RESULT");
    }
    return result;
  }
}

export function buildAdmissionRequest(
  context: GovernedActionContext,
  envelope: Record<string, unknown>,
): AdmissionEnvelopeRequest {
  if (!context.candidateId || !context.contractId || !context.workflowId || !context.nodeId || !context.inputSnapshotId) {
    throw new GovernedActionError("Admission context is incomplete", "INCOMPLETE_ADMISSION_CONTEXT");
  }
  if (!Number.isInteger(context.contractVersion) || !validDigest(context.contractDigest)) {
    throw new GovernedActionError("Admission contract identity is invalid", "INVALID_CONTRACT_IDENTITY");
  }
  return { candidateId: context.candidateId, context, envelope: structuredClone(envelope) };
}

export class GovernedActionController {
  constructor(private readonly client: GovernedAdmissionClient) {}

  async execute(
    context: GovernedActionContext,
    envelope: Record<string, unknown>,
    runtime?: RuntimeView,
    signal?: AbortSignal,
  ): Promise<GovernedActionResult> {
    const request = buildAdmissionRequest(context, envelope);
    let assessment: AdmissionAssessmentResult;
    try {
      assessment = await this.client.assess(request, signal);
    } catch (error) {
      assessment = {
        status: "error",
        admitted: false,
        reason: error instanceof Error ? error.message : "assessment_failed",
      };
    }

    const result: GovernedActionResult = {
      action: context.action,
      assessment,
      // The browser never commits to PEB. PEB authority is invoked by the
      // server-side admission path only after this assessment is persisted.
      pebAuthorityInvoked: false,
    };
    if (runtime) {
      const event: RuntimeEvent = {
        type: "governed-action-result",
        source: "governed-action-controller",
        payload: result as unknown as Record<string, unknown>,
        timestamp: Date.now(),
      };
      runtime.eventBus.emit(event);
    }
    return result;
  }
}

export function resultLabel(result: AdmissionAssessmentResult): string {
  if (result.status === "admitted" && result.admitted) return "Admitted";
  if (result.status === "unknown") return "Unknown — more context required";
  if (result.status === "stale") return "Stale doctrine";
  if (result.status === "drift") return "Drift detected";
  if (result.status === "error") return "Assessment error";
  return `Refused: ${result.reason}`;
}

export function assertAssessmentIdentity(result: AdmissionAssessmentResult): void {
  if (result.admitted && (!result.evaluationFingerprint || !result.admissionReceiptId)) {
    throw new GovernedAdapterError(
      "Admitted result must carry evaluation fingerprint and admission receipt identity",
      "INCOMPLETE_ADMISSION_RESULT",
    );
  }
}
