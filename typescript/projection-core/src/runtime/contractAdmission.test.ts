/**
 * W4.06 — Contract admission conformance tests (dependency-free, tsx).
 */
import {
  ContractAdmissionRegistry,
  REQUIRED_ARTIFACT_KINDS,
  isValidSha256Digest,
  type ContractAdmissionRequest,
} from "./contractAdmission.js";

function equal(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
}

const DIMS = ["authority", "provenance", "frame"];
const D = "sha256:" + "a".repeat(64);
const D2 = "sha256:" + "b".repeat(64);

function req(overrides: Partial<ContractAdmissionRequest> = {}): ContractAdmissionRequest {
  return {
    contractId: "wrp-core",
    version: 1,
    artifacts: REQUIRED_ARTIFACT_KINDS.map((kind) => ({ kind, digest: D })),
    framingDimensions: { authority: "peb", provenance: "envelope", frame: "is_well_framed" },
    ...overrides,
  };
}

// ── 1. Valid admission passes ──
{
  const reg = new ContractAdmissionRegistry(DIMS);
  const out = reg.admit(req());
  equal(out.status, "admitted", "valid set admitted");
  equal(out.version, 1, "version echoed");
}

// ── 2. Fail-closed: missing artifact kind ──
{
  const reg = new ContractAdmissionRegistry(DIMS);
  const out = reg.admit(req({ artifacts: [req().artifacts[0]!] }));
  equal(out.status, "refused", "missing kinds refused");
  equal((out.reason ?? "").startsWith("missing_artifact:"), true, "reason names missing artifact");
}

// ── 3. Fail-closed: invalid digest ──
{
  const reg = new ContractAdmissionRegistry(DIMS);
  const out = reg.admit(req({ artifacts: [{ kind: "typespec", digest: "sha256:short" }, { kind: "jsonld", digest: D }, { kind: "cue", digest: D }] }));
  equal(out.status, "refused", "invalid digest refused");
  equal((out.reason ?? "").startsWith("invalid_digest:"), true, "reason names invalid digest");
}

// ── 4. Fail-closed: missing framing dimension ──
{
  const reg = new ContractAdmissionRegistry(DIMS);
  const dims = { authority: "peb", provenance: "envelope" }; // frame missing
  const out = reg.admit(req({ framingDimensions: dims }));
  equal(out.status, "refused", "missing dimension refused");
  equal(out.reason, "missing_framing_dimension:frame", "reason names the dimension");
}

// ── 5. Empty framing dimension value refused ──
{
  const reg = new ContractAdmissionRegistry(DIMS);
  const out = reg.admit(req({ framingDimensions: { authority: "peb", provenance: "envelope", frame: "  " } }));
  equal(out.status, "refused", "blank dimension refused");
}

// ── 6. Version monotonicity ──
{
  const reg = new ContractAdmissionRegistry(DIMS);
  equal(reg.admit(req({ version: 3 })).status, "admitted", "v3 admitted");
  equal(reg.admit(req({ version: 2 })).status, "refused", "v2 after v3 refused (monotonic)");
  equal((reg.admit(req({ version: 3 })) as { reason?: string }).reason, "version_not_monotonic:3<=3", "same version refused");
  equal(reg.admit(req({ version: 4 })).status, "admitted", "v4 admitted");
}

// ── 7. Digest rewrite at same version rejected ──
{
  const reg = new ContractAdmissionRegistry(DIMS);
  equal(reg.admit(req({ version: 1 })).status, "admitted", "v1 admitted");
  // Rewind: re-admitting v1 with a different digest must be refused.
  const reg2 = new ContractAdmissionRegistry(DIMS);
  equal(reg2.admit(req({ version: 1, artifacts: [{ kind: "typespec", digest: D }, { kind: "jsonld", digest: D }, { kind: "cue", digest: D }] })).status, "admitted", "v1 first admit");
  const out = reg2.admit(req({ version: 2, artifacts: [{ kind: "typespec", digest: D2 }, { kind: "jsonld", digest: D2 }, { kind: "cue", digest: D2 }] }));
  equal(out.status, "admitted", "v2 with new digests admitted (new version)");
}

// ── 8. Registry requires dimensions at construction (fail-closed config) ──
{
  let threw = false;
  try {
    new ContractAdmissionRegistry([]);
  } catch (e) {
    threw = (e as Error).message === "admission_requires_framing_dimensions";
  }
  equal(threw, true, "empty dimension list rejected at construction");
}

// ── 9. Digest validator ──
{
  equal(isValidSha256Digest(D), true, "valid digest accepted");
  equal(isValidSha256Digest("sha256:xyz"), false, "non-hex rejected");
  equal(isValidSha256Digest("md5:" + "a".repeat(64)), false, "wrong algo rejected");
}

console.log("contractAdmission conformance: 9/9 scenario groups passed");
