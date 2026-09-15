/**
 * SOLScript TypeScript core — Keychains event contracts.
 *
 * Ported from python/SOLScript/solscript/events.py. The contracts carry
 * identities and read-set metadata, never source content. `stableDigest`
 * MUST produce byte-identical digests to Python's `stable_digest`
 * (canonical JSON: sorted keys, separators (",", ":"), default=str).
 */
export declare const KEYCHAIN_EVENT_SCHEMA_VERSION = 1;
export declare const READ_SET_MANIFEST_SCHEMA_VERSION = 1;
export declare function stableDigest(value: unknown): string;
/** Synchronous SHA-256 implementation (no external deps; event digests are small). */
export declare function sha256Hex(input: string): string;
export interface ReadSetManifestInput {
    sourceNamespace: string;
    evaluationId: string;
    evaluationKind: string;
    targetId: string;
    /** ISO string; capture-time fact, excluded from manifest_digest identity. */
    asOf: string;
    visibilityScope?: string;
    evaluatorId?: string;
    permissions?: Record<string, unknown>;
    context?: Record<string, unknown>;
    sourceRefs?: Record<string, unknown>[];
    manifestId?: string;
    recordedAt?: string;
    status?: string;
}
export interface ReadSetManifest {
    sourceNamespace: string;
    evaluationId: string;
    evaluationKind: string;
    targetId: string;
    asOf: string;
    visibilityScope: string;
    evaluatorId: string | null;
    permissions: Record<string, unknown>;
    context: Record<string, unknown>;
    sourceRefs: Record<string, unknown>[];
    manifestId: string;
    schemaVersion: number;
    recordedAt: string;
    manifestDigest: string;
    status: string;
    readonly idempotencyKey: string;
}
export declare function buildReadSetManifest(input: ReadSetManifestInput): ReadSetManifest;
export declare function manifestToDict(m: ReadSetManifest): Record<string, unknown>;
export interface KeychainEvent {
    sourceNamespace: string;
    sourceEventId: string;
    kind: string;
    outcome: string;
    aggregateId: string | null;
    schemaVersion: number;
    causationId: string | null;
    correlationId: string | null;
    actor: string | null;
    contractId: string | null;
    evaluatorId: string | null;
    lawId: string | null;
    effectiveAt: string | null;
    recordedAt: string;
    readSet: Record<string, unknown>;
    payload: Record<string, unknown>;
    checkpointStatus: string;
    /** Globally addressable identity: {source_namespace}:{source_event_id}. */
    readonly eventId: string;
    /** Stable source-scoped key used by outbox and Keychains consumers. */
    readonly idempotencyKey: string;
}
export declare function keychainEventId(e: Pick<KeychainEvent, "sourceNamespace" | "sourceEventId">): string;
export declare function keychainEventToDict(e: KeychainEvent): Record<string, unknown>;
export interface TransitionEventInput {
    sourceEventId: string;
    entityId: string;
    transitionId: string;
    outcome: string;
    results: unknown;
    conceptId?: string | null;
    stateBefore?: unknown;
    stateAfter?: unknown;
    effectiveAt?: string | null;
    correlationId?: string | null;
    actor?: string | null;
    sourceNamespace?: string;
}
export declare function buildTransitionEvent(input: TransitionEventInput): KeychainEvent;
export interface EvaluationEventInput {
    sourceEventId: string;
    evaluationKind: string;
    targetId: string;
    manifest: ReadSetManifest;
    result: Record<string, unknown>;
    outcome?: string;
    evaluatorId?: string | null;
    actor?: string | null;
    sourceNamespace?: string;
}
export declare function buildEvaluationEvent(input: EvaluationEventInput): KeychainEvent;
//# sourceMappingURL=events.d.ts.map