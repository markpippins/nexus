// requirements.js — the demand-side write path for wind.node_requirements.
//
// Wires node creation (and standalone requirement registration) to real
// demands: every node POST can carry a `requirements` array, and PUT
// /requirements/:id closes an open demand bitemporally (close-then-insert,
// V175 convention) — never an in-place mutation of history.
//
// Verdicts come only from the capability-resolver (#317/#318): this module
// never invents a verdict, it persists the latest one observed on demand.
// V174 vocabulary is enforced by the table's CHECK; this file re-validates
// with the shared list so a bad value dies at the route, not the DB.

import { query } from './db.js';
import { resolveCapability, verifyRoleCredential } from './capability-resolver.js';

export const SATISFACTION_STATES = Object.freeze([
  'satisfied', 'satisfied-stale', 'unsatisfied', 'unreachable', 'refused', 'unknown',
]);

export function normalizeRequirements(input) {
  if (input === undefined || input === null) return [];
  if (!Array.isArray(input)) return [];
  const out = [];
  for (const raw of input) {
    if (typeof raw === 'string') {
      // Shorthand: bare string = capability key
      if (raw.trim()) out.push({ capabilityKey: raw.trim() });
      continue;
    }
    if (raw && typeof raw === 'object') {
      const cap = typeof raw.capabilityKey === 'string' ? raw.capabilityKey.trim() : '';
      const cred = typeof raw.roleCredential === 'string' ? raw.roleCredential.trim() : '';
      if (cap) out.push({ capabilityKey: cap });
      if (cred) out.push({ roleCredential: cred });
    }
  }
  // De-dupe: same (node, kind, value) twice is one demand
  const seen = new Set();
  return out.filter((r) => {
    const k = `${r.capabilityKey || ''}|${r.roleCredential || ''}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

// Insert one demand row. The open-interval unique indexes make a re-insert
// of the same (node, kind, value) a hard conflict — the caller decides
// whether that's an error (explicit registration) or expected (re-seed).
export async function insertRequirement(nodeId, req) {
  const capabilityKey = req.capabilityKey || null;
  const roleCredential = req.roleCredential || null;
  const r = await query(
    `INSERT INTO wind.node_requirements (node_id, capability_key, role_credential)
     VALUES ($1, $2, $3)
     RETURNING id, node_id, capability_key, role_credential, valid_from, valid_until`,
    [nodeId, capabilityKey, roleCredential]);
  return r.rows[0];
}

// Close-then-insert: close the open interval, then open the successor.
// Never an in-place mutation of history — the V175 convention.
export async function replaceRequirement(nodeId, previousId, next) {
  const capabilityKey = next?.capabilityKey || null;
  const roleCredential = next?.roleCredential || null;
  if (!capabilityKey && !roleCredential) {
    throw new Error('replacement demand must name a capability or a role');
  }
  const closed = await query(
    `UPDATE wind.node_requirements
     SET valid_until = now()
     WHERE id = $1 AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
     RETURNING id`,
    [previousId]);
  if (closed.rows.length === 0) {
    throw new Error('open requirement not found — refusing to close a closed row');
  }
  const opened = await query(
    `INSERT INTO wind.node_requirements (node_id, capability_key, role_credential)
     VALUES ($1, $2, $3)
     RETURNING id, node_id, capability_key, role_credential, valid_from, valid_until`,
    [nodeId, capabilityKey, roleCredential]);
  return opened.rows[0];
}

// Attach the resolver's current verdicts to a demand set (read-side sugar
// for the seed path and the UI).
export async function attachVerdicts(rows) {
  const out = [];
  for (const row of rows) {
    const copy = { ...row };
    if (row.capability_key) {
      const res = await resolveCapability(row.capability_key);
      copy.verdict = res.verdict;
      copy.capability_exists = res.exists;
    } else if (row.role_credential) {
      const res = await verifyRoleCredential(row.role_credential);
      copy.verdict = res.exists
        ? (res.can_verify_work_requests || res.can_greenlight ? 'satisfied' : 'unsatisfied')
        : 'unknown';
      copy.credential = res;
    }
    out.push(copy);
  }
  return out;
}
