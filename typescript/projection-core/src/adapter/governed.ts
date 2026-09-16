import { CapabilityId } from "../types/designIR";
import { ArtifactIdentity, PayloadSource, ViewSpec, validateViewSpec } from "../types/viewSpec";

export interface ProjectionManifest extends ArtifactIdentity {
  schemaVersion: 1;
  contractId: string;
  operation: string;
  outputContract: CapabilityId;
  source: "server";
}

export interface GovernedProjection<T = unknown> {
  manifest: ProjectionManifest;
  data: T;
  receivedAt: string;
}

export interface GovernedSourceClient {
  fetchProjection<T = unknown>(
    source: GovernedSource,
    signal?: AbortSignal,
  ): Promise<GovernedProjection<T>>;
}

export interface GovernedSource {
  type: "server";
  url: string;
  manifest: ProjectionManifest;
}

export class GovernedAdapterError extends Error {
  constructor(message: string, public readonly code: string) {
    super(message);
    this.name = "GovernedAdapterError";
  }
}

function isDigest(value: unknown): value is `sha256:${string}` {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}

export function validateProjectionManifest(value: unknown): value is ProjectionManifest {
  if (!value || typeof value !== "object") return false;
  const manifest = value as Partial<ProjectionManifest>;
  return manifest.schemaVersion === 1
    && manifest.source === "server"
    && typeof manifest.artifactId === "string"
    && Number.isInteger(manifest.artifactVersion)
    && isDigest(manifest.artifactDigest)
    && typeof manifest.contractId === "string"
    && typeof manifest.operation === "string"
    && typeof manifest.outputContract === "string";
}

export function assertGovernedSource(source: PayloadSource): asserts source is GovernedSource {
  if (source.type !== "server") {
    throw new GovernedAdapterError(
      "Governed projections require a server-controlled source",
      "SOURCE_NOT_GOVERNED",
    );
  }
  if (!source.url || !/^https?:\/\//.test(source.url)) {
    throw new GovernedAdapterError("Governed source URL must be absolute HTTP(S)", "INVALID_SOURCE_URL");
  }
  if (!validateProjectionManifest(source.manifest)) {
    throw new GovernedAdapterError("Projection manifest is invalid", "INVALID_PROJECTION_MANIFEST");
  }
}

export class FetchGovernedSourceClient implements GovernedSourceClient {
  constructor(private readonly fetcher: typeof fetch = fetch) {}

  async fetchProjection<T = unknown>(
    source: GovernedSource,
    signal?: AbortSignal,
  ): Promise<GovernedProjection<T>> {
    assertGovernedSource(source);
    const response = await this.fetcher(source.url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        "X-ViewSpec-Artifact": source.manifest.artifactId,
        "X-ViewSpec-Version": String(source.manifest.artifactVersion),
        "X-ViewSpec-Digest": source.manifest.artifactDigest,
      },
      signal,
    });
    if (!response.ok) {
      throw new GovernedAdapterError(
        `Governed projection request failed: HTTP ${response.status}`,
        "SOURCE_REQUEST_FAILED",
      );
    }

    const body: unknown = await response.json();
    if (!body || typeof body !== "object") {
      throw new GovernedAdapterError("Projection response must be an object", "INVALID_PROJECTION_RESPONSE");
    }
    const envelope = body as { manifest?: unknown; data?: unknown; receivedAt?: unknown };
    if (!validateProjectionManifest(envelope.manifest)
      || envelope.manifest.artifactDigest !== source.manifest.artifactDigest
      || envelope.manifest.artifactVersion !== source.manifest.artifactVersion) {
      throw new GovernedAdapterError(
        "Projection response manifest does not match the requested artifact",
        "PROJECTION_IDENTITY_MISMATCH",
      );
    }
    if (typeof envelope.receivedAt !== "string") {
      throw new GovernedAdapterError("Projection response is missing receivedAt", "INVALID_PROJECTION_RESPONSE");
    }
    return {
      manifest: envelope.manifest,
      data: envelope.data as T,
      receivedAt: envelope.receivedAt,
    };
  }
}

export function governedSourceFromViewSpec(spec: ViewSpec, bindingId: string): GovernedSource {
  if (!validateViewSpec(spec)) {
    throw new GovernedAdapterError("Cannot derive source from invalid ViewSpec", "INVALID_VIEWSPEC");
  }
  const binding = spec.adapters.find((candidate) => candidate.adapterId === bindingId);
  if (!binding || binding.source.type !== "server") {
    throw new GovernedAdapterError(
      "ViewSpec binding must reference a server-controlled source",
      "SOURCE_NOT_GOVERNED",
    );
  }
  assertGovernedSource(binding.source);
  return binding.source;
}
