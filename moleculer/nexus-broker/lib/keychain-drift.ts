/**
 * Agent-record drift between two keychain checkpoints.
 *
 * Drift answers one question — what moved since the previous checkpoint — and
 * it is computed against the **prior instance index**, which is a flat
 * `instance_id -> record_id` map (`indexFromManifests` / the bounded
 * `checkpoint_heads` pointer). Both sides must therefore be keyed by
 * `instance_id`.
 *
 * The entries map `projectAgentRecords` holds is keyed differently: its keys
 * carry the historical entry prefixes, `single:<uuid>`,
 * `amendment:<token>` and `explicit:<prefix>`, while `KeychainEntry.instance_id`
 * is unprefixed. Comparing the map's keys against the index's keys means no
 * instance ever matches, every instance reports `new_instance` on every
 * checkpoint, and the `superseded` branch can never fire. This module exists
 * so that comparison has exactly one implementation and one test.
 */

export type DriftKind = "new_instance" | "superseded";

/** The slice of a resolved keychain entry that drift needs. */
export interface DriftEntry {
  instance_id: string;
  record_type: string;
  role: string;
  current: {
    record_id: string;
    version: number;
  };
}

/** Prior state: instance ids and record ids present at the previous checkpoint. */
export interface DriftPriorState {
  instanceIds: ReadonlySet<string>;
  recordIds: ReadonlySet<string>;
}

export interface DriftFinding {
  snapshot_version: number;
  kind: DriftKind;
  instance_id: string;
  record_type: string;
  role: string;
  /** Present only on `superseded` findings. */
  new_record_id?: string;
  /** Present only on `superseded` findings. */
  new_version?: number;
  description: string;
  severity: "info";
  detected_at: string;
}

/**
 * Compute drift findings for the current entry set.
 *
 * @param entries current entries (pass the map's `.values()`, never its keys)
 * @param prior   prior index state — instance ids and record ids
 * @param version checkpoint version these findings belong to
 * @param now     ISO timestamp recorded on each finding
 */
export function computeAgentRecordDrift(
  entries: Iterable<DriftEntry>,
  prior: DriftPriorState,
  version: number,
  now: string,
): DriftFinding[] {
  const findings: DriftFinding[] = [];

  for (const entry of entries) {
    const instanceId = entry.instance_id;

    if (!prior.instanceIds.has(instanceId)) {
      findings.push({
        snapshot_version: version,
        kind: "new_instance",
        instance_id: instanceId,
        record_type: entry.record_type,
        role: entry.role,
        description: `New keychain instance ${instanceId} (${entry.record_type}/${entry.role})`,
        severity: "info",
        detected_at: now,
      });
      continue;
    }

    if (!prior.recordIds.has(entry.current.record_id)) {
      findings.push({
        snapshot_version: version,
        kind: "superseded",
        instance_id: instanceId,
        record_type: entry.record_type,
        role: entry.role,
        new_record_id: entry.current.record_id,
        new_version: entry.current.version,
        description: `Instance ${instanceId} superseded: now v${entry.current.version} (${entry.current.record_id})`,
        severity: "info",
        detected_at: now,
      });
    }
  }

  return findings;
}

/**
 * Derive the prior state from a stored index (`checkpoint_heads.index`), the
 * same way `projectAgentRecords` derives it before calling
 * `computeAgentRecordDrift`.
 */
export function priorStateFromIndex(index: Record<string, string> | null | undefined): DriftPriorState {
  const entries = Object.entries(index || {});
  return {
    instanceIds: new Set(entries.map(([instanceId]) => instanceId)),
    recordIds: new Set(entries.map(([, recordId]) => recordId)),
  };
}
