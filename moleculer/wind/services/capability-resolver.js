// capability-resolver.js — the NodeRequirement resolver behind the #314 contract.
//
// Consumes the LIVE V172/V174 surfaces (read-only):
//   nebula.capabilities              — the demand atoms (by name)
//   nebula.v_capability_satisfaction — provider state incl. satisfaction_state
//   nebula.roles_history             — bitemporal credential verification (V175)
//
// Emits the contract vocabulary (#314): SatisfactionVerdict (V174-exact,
// six states, never collapsed), CapabilityBinding, ResolvedContextBundle
// (resolved references only — never embedded content).
//
// WARN-MODE posture (staged ladder): the resolver observes and reports; it
// never blocks. Callers may surface verdicts, but no route here gates a
// workflow transition on a verdict until an operator enforce flip.

import { query } from './db.js';

// The staged-ladder mode seam (P1, To Do 0577c018): warn until the operator
// sets WIND_RESOLVER_MODE=enforce. Both the response `mode` field and the
// resolver-check journal lines read this ONE source — never a literal.
export function resolverMode() {
  return process.env.WIND_RESOLVER_MODE === 'enforce' ? 'enforce' : 'warn';
}

// The six-state V174 vocabulary, mirrored for validation at the boundary.
export const SATISFACTION_STATES = [
  'satisfied', 'satisfied-stale', 'unsatisfied', 'unreachable', 'refused', 'unknown',
];

// Pure classifier: an unknown or absent state maps to 'unknown' — the
// vocabulary is never collapsed and never guessed.
export function classifyVerdict(state) {
  return SATISFACTION_STATES.includes(state) ? state : 'unknown';
}

export const VERIFY_HOLDER_DOMAINS = 'test-verification'; // informational only

// The verify-holder class (RoleAlias proposal e9f81ae7): the roles holding
// can_verify_work_requests at evaluation time. Read live, never hard-coded.
async function verifyHolders() {
  const r = await query(
    `SELECT name FROM nebula.roles
     WHERE can_verify_work_requests
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz
     ORDER BY name`);
  return r.rows.map((x) => x.name);
}

// Resolve one capability demand to a V174 verdict. Never collapses a
// non-satisfied case: whatever v_capability_satisfaction says, passes through.
export async function resolveCapability(capabilityKey) {
  const cap = await query(
    `SELECT id, name, description FROM nebula.capabilities WHERE name = $1`,
    [capabilityKey]);
  if (cap.rows.length === 0) {
    return { capability: capabilityKey, exists: false, verdict: 'unknown',
             satisfying_providers: [], evidenceRefs: [] };
  }
  const sat = await query(
    `SELECT satisfaction_state, satisfying_providers, active_adapters,
            degraded_adapters, declared_adapters, last_observed_at, evidence_age,
            concept_id
     FROM nebula.v_capability_satisfaction WHERE capability = $1`,
    [capabilityKey]);
  const row = sat.rows[0] || {};
  const verdict = classifyVerdict(row.satisfaction_state);
  return {
    capability: capabilityKey,
    exists: true,
    verdict,
    satisfying_providers: row.satisfying_providers || [],
    adapter_counts: {
      active: row.active_adapters, degraded: row.degraded_adapters,
      declared: row.declared_adapters,
    },
    last_observed_at: row.last_observed_at || null,
    concept_id: row.concept_id || null,
    evidenceRefs: row.last_observed_at
      ? [`v_capability_satisfaction:${capabilityKey}@${row.last_observed_at.toISOString()}`]
      : [],
  };
}

// Verify a role credential against the live bitemporal open snapshot (V175
// shape): the role must exist with an open recorded interval.
export async function verifyRoleCredential(roleName) {
  const r = await query(
    `SELECT can_verify_work_requests, can_greenlight, owns_domains
     FROM nebula.roles
     WHERE name = $1
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz`,
    [roleName]);
  if (r.rows.length === 0) {
    return { role: roleName, exists: false, can_verify_work_requests: false,
             can_greenlight: false, owns_domains: [] };
  }
  const row = r.rows[0];
  return {
    role: roleName, exists: true,
    can_verify_work_requests: row.can_verify_work_requests,
    can_greenlight: row.can_greenlight,
    owns_domains: row.owns_domains || [],
  };
}

// Resolve all requirements attached to a node. Pure read-side; returns the
// per-requirement verdicts plus the class-resolved role candidates.
export async function resolveNodeRequirements(nodeId) {
  const node = await query(
    `SELECT n.id, n.name, n.workflow_version_id
     FROM wind.workflow_nodes n WHERE n.id = $1`,
    [nodeId]);
  if (node.rows.length === 0) return null;

  const reqs = await query(
    `SELECT capability_key, role_credential, last_verdict
     FROM wind.node_requirements WHERE node_id = $1 ORDER BY capability_key`,
    [nodeId]);

  const holders = reqs.rows.length ? await verifyHolders() : [];
  const resolutions = [];
  for (const req of reqs.rows) {
    const capRes = req.capability_key
      ? await resolveCapability(req.capability_key)
      : null;
    const credential = req.role_credential
      ? await verifyRoleCredential(req.role_credential)
      : null;
    // Per-kind verdict: a credential demand is judged by whether the named
    // role holds a lawful open snapshot with real authority (a live row that
    // exists but holds nothing is `unsatisfied`, not `satisfied`); a
    // capability demand keeps its V174 verdict verbatim. Combined demands
    // (both kinds on one row — not produced by the seeder) must BOTH hold.
    let verdict;
    if (req.capability_key && credential) {
      const credOk = credential.exists &&
        (credential.can_verify_work_requests || credential.can_greenlight ||
         (credential.owns_domains || []).length > 0);
      verdict = (capRes.verdict === 'satisfied' && credOk) ? 'satisfied' : 'unsatisfied';
    } else if (req.capability_key) {
      verdict = capRes.verdict;
    } else if (credential) {
      const credOk = credential.exists &&
        (credential.can_verify_work_requests || credential.can_greenlight ||
         (credential.owns_domains || []).length > 0);
      verdict = credOk ? 'satisfied' : 'unsatisfied';
    } else {
      verdict = 'unknown';
    }
    resolutions.push({
      capability_key: req.capability_key,
      capability: capRes,
      role_credential: credential,
      // verify-holder class resolution (RoleAlias e9f81ae7): which live roles
      // could satisfy a verify-shaped demand right now.
      verify_holder_class: holders,
      effective_verdict: verdict,
    });
  }
  return {
    node: { id: node.rows[0].id, name: node.rows[0].name,
            workflow_version_id: node.rows[0].workflow_version_id },
    requirements: resolutions,
    mode: resolverMode(), // staged ladder: warn until operator enforce flip
  };
}

// Pure bundle assembly from resolved requirements: RESOLVED REFERENCES ONLY.
// This function never carries content blobs — card slugs, digest refs,
// record ids, work refs.
export function buildBundle(node, requirements, mode = resolverMode()) {
  return {
    node,
    mode,
    requirement_verdicts: requirements.map((r) => ({
      capability_key: r.capability_key,
      verdict: r.effective_verdict,
    })),
    binding_refs: {
      capability_concepts: requirements
        .map((r) => r.capability && r.capability.concept_id).filter(Boolean),
      procedure_card_refs: [],  // card slugs land here when wired (refs only)
      digest_refs: [],          // continuity digest refs land here (refs only)
      connection_record_refs: [], // V169 census refs land here (refs only)
    },
    generated_at: new Date().toISOString(),
  };
}

// Assemble the ResolvedContextBundle for a node (read-side).
export async function resolveContextBundle(nodeId) {
  const resolved = await resolveNodeRequirements(nodeId);
  if (!resolved) return null;
  return buildBundle(resolved.node, resolved.requirements, resolved.mode);
}
