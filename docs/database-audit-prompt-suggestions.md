# Suggestions for `dba-role-system-prompt-working.md`

These suggestions come from applying the prompt to the September 2026 Nexus catalog, not from a proposed product redesign. They aim to make a complete audit more repeatable, less prone to false positives, and clearer about what its evidence proves.

## High-value changes

1. **Require a compact machine-readable evidence appendix.** For every CRITICAL/GAP, record the exact read-only query (or a stable script path), its row count, PostgreSQL version, database name, role, and audit timestamp. The current prose-oriented prompt is strong on questions but weak on reproducibility; it makes later comparison unnecessarily manual.

2. **Make replay testing a mandatory three-way check.** The prompt says to run a drift function when present, but should require: (a) count ledger rows, (b) count projection rows, and (c) aggregate comparator mismatches by field. That would have exposed the Conduit condition immediately: state values looked plausible, but all 26 rows had a missing replayed `last_event_id`.

3. **Add a result-classification rule for trigger-function scans.** The current naming-pattern instruction produces false-positive candidates for historical functions and `INSTEAD OF` view triggers. Require the report to classify each candidate as `base-table guard`, `view-write handler`, `historical/replaced`, or `unclassified`, and only elevate an unclassified base-table guard after checking `pg_trigger`, the owning view/table, and current callers.

4. **Explicitly require a privilege check for every claimed immutable/audit table.** An attached rejection trigger alone does not establish a sole write surface, and a table with clean data plus `UPDATE`/`DELETE` grants is still convention-bound. The prompt names this principle, but a mandatory pair of checks—trigger attachment plus `has_table_privilege` for the application role—would make reports comparable.

5. **Define how to handle service-state contradictions.** The prompt asks for subscriber-process verification, but managed-service status can be unavailable while PostgreSQL has active listener sessions. Add: report service-manager evidence and database-session evidence separately; do not infer an outage from either alone; treat the absence of a durable outbox/publish record as the database-level gap.

6. **Add an audit-scope inventory step.** The schema list in the prompt is necessarily dated; this database now includes schemas such as `absorb`, `aegis`, `aspects`, `assembly`, `gateway`, `jenkins`, `mcp`, `shrapnel`, `sonar`, `steward`, `throttler`, `voyager`, and probe/debug schemas. Require a catalog inventory and a disposition for unknown schemas (product, extension, test, temporary, or unclassified) before relying on the prose list.

7. **Separate live facts from source-only findings in the required template.** The prior Resolution report correctly labeled itself static when PostgreSQL was unavailable. Make that structural: every finding should carry `Evidence: live`, `source`, or `both`, and source-only findings must state the exact live verification still required.

8. **Provide a degraded-governance fallback that is consistent with database-first doctrine.** The prompt says to create a Nebula record when available and otherwise write markdown, while workspace doctrine requires an intent record before substantive work. Add a specified fallback receipt queue or a mandatory “record creation unavailable” section with retry instructions, so failure of the record API does not silently erase the audit trail.

9. **Define a bounded cadence for the expensive universal scans.** “Every schema, every pass” is valuable but will become impractical as the catalog grows. Retain mandatory high-risk checks every run (projection drift, audit immutability, expiry, write-surface privileges, notification/outbox health), and rotate exhaustive orphan/duplicate/vocabulary scans by schema with a stated maximum revisit interval.

10. **Add remediation verification language without authorizing fixes.** The prompt rightly prohibits unilateral schema changes. It should still require a later audit to state the migration/version expected to remediate a finding and to verify its attachment, privileges, existing-data backfill, and negative-path behavior—not merely that the migration file exists.

## Small wording corrections

- Replace “all schemas” followed by an example list with “discover schemas from `pg_namespace`; the list below is illustrative.”
- For notification checks, distinguish durable database state (outbox/publish log) from ephemeral `pg_notify` delivery.
- State that a zero-row result is only meaningful when the query’s population is recorded—for example, `0 active expired leases out of N active leases`.
- Add a short confidentiality rule: reports should use counts, IDs only when operationally necessary, and no credential or secret values.

## Suggested report metadata

Add a required header with: database/server version, connected role, source commit, migration-ledger version(s), schemas/relations inventoried, service-manager status, audit-record receipt UUID or degraded-mode reason, and the prior report reference. This turns “Since Last Run” into evidence-based continuity instead of a narrative reconstruction.
