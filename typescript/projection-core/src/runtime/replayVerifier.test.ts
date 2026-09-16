function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function deepEqual(left: unknown, right: unknown, message: string): void {
  if (JSON.stringify(left) !== JSON.stringify(right)) throw new Error(message);
}

import {
  applyMutation,
  classifyDrift,
  driftVerdict,
  envelopeFingerprint,
  replayEnvelope,
  type ReplayFixture,
} from "./replayVerifier.js";

export interface ReplayFixtureDocument {
  fixture_id: string;
  attempts: Array<{ envelope: ReplayFixture["envelope"] }>;
  expected_outcomes: ReplayFixture["expected"][];
  law_registry?: ReplayFixture["law_registry"];
  contract_registry?: ReplayFixture["contract_registry"];
  retry_after_admission?: boolean;
  prior_admission?: {
    envelopeId: string;
    evaluationFingerprint: string;
    pebTransactionId?: string;
    admissionReceiptId?: string;
    consumedAt?: string;
  } | null;
}

const expected: Record<string, string[]> = {
  F01_allow_with_receipt: ["replay_ok"],
  F02_reject_plain: ["replay_ok"],
  F03_refuse_unknown_context: ["replay_ok"],
  F04_stale_doctrine: ["stale_doctrine"],
  F05_contract_digest_drift: ["drift_confirmed"],
  F06_duplicate_retry: ["replay_ok", "duplicate_retry"],
  F07_doctrine_change_mid_workflow: ["replay_ok", "stale_doctrine"],
};

/** Run the W1.09 fixture corpus supplied by the caller, with no filesystem or network access. */
export async function runReplayConformance(documents: ReplayFixtureDocument[]): Promise<void> {
  for (const document of documents) {
    const views = document.attempts.map((attempt, index): ReplayFixture => ({
      envelope: attempt.envelope,
      expected: document.expected_outcomes[index]!,
      law_registry: document.law_registry,
      contract_registry: document.contract_registry,
      priorAdmission: index > 0
        ? document.prior_admission ?? {
          envelopeId: String(attempt.envelope.envelope_id),
          evaluationFingerprint: attempt.envelope.fingerprint.evaluation_fingerprint,
        }
        : null,
    }));
    const results = await Promise.all(views.map(replayEnvelope));
    const actualVerdicts = results.map((result) => result.verdict);
    if (JSON.stringify(actualVerdicts) !== JSON.stringify(expected[document.fixture_id])) {
      throw new Error(`${document.fixture_id}: ${JSON.stringify(actualVerdicts)}`);
    }
    for (const [index, view] of views.entries()) {
      const first = await replayEnvelope(view);
      const second = await replayEnvelope(structuredClone(view));
      deepEqual(first, second, `${document.fixture_id}[${index}] replay must be deterministic`);
    }
  }

  const f01 = documents.find((document) => document.fixture_id === "F01_allow_with_receipt");
  assert(f01, "F01 fixture is required");
  const base: ReplayFixture = {
    envelope: f01.attempts[0]!.envelope,
    expected: f01.expected_outcomes[0]!,
    law_registry: f01.law_registry,
    contract_registry: f01.contract_registry,
  };
  assert(await envelopeFingerprint(base.envelope) === base.envelope.fingerprint.evaluation_fingerprint, "fingerprint roundtrip");
  assert(classifyDrift("authority.peb_transaction_id") === "receipt_lineage", "lineage category");
  try { classifyDrift("unknown.path"); throw new Error("unknown mutation path must fail"); } catch (error) { if (error instanceof Error && error.message === "unknown mutation path must fail") throw error; }
  assert(JSON.stringify(applyMutation(base.envelope, "evaluation.disposition", "Changed")) !== JSON.stringify(base.envelope), "mutation must change a copy");
  assert((await driftVerdict(base, "evaluation.disposition", "Changed")).verdict === "drift_confirmed", "drift verdict");
}
