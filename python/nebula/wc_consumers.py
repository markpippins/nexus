"""W-C consumer inventory and writer-fence registry (plan 8261665).

Plan 8261665 is the re-run of plan 8261655 after the work-evidence gate
landed. The acceptance criteria are unchanged; the census was re-verified
against the live database on 2026-10-30 before re-dispatch.

This module is the machine-readable twin of
``docs/promotion-flow/w-c-consumer-migration.md``. It is deliberately
**read-only**: it classifies the consumers of promotion identity, names the
single write-path fence each one must cross, and defines the Stage D gate
checklist that closes each package. It never writes a canonical table.

Promotion identity, for W-C purposes, is the four-part key:

    candidate_id       nebula.harvest_candidates.id (uuid)
    candidate_set_key  sha256 over the sorted candidate-id set
    asset_key          semantics.canonical_asset.canonical_asset_id
                       (asset:nexus:nebula_harvest_candidates:<candidate_id>)
    requirement_id     nebula.requirements.candidate_id

A *consumer* is a **write-path boundary**, not a single file. Every file that
reads or writes through the same boundary is listed under exactly one package;
a file that touches a second boundary declares it in ``also_touches``. That is
what makes the packages M-sized and independently reviewable while keeping the
"one consumer -> one package" rule honest.

CLI (from ``nexus/``)::

    PYTHONPATH=python python3 -m nebula.wc_consumers            # print JSON
    PYTHONPATH=python python3 -m nebula.wc_consumers --validate # structure check
    PYTHONPATH=python python3 -m nebula.wc_consumers --census   # live read-only census
    PYTHONPATH=python python3 -m nebula.wc_consumers --write PATH

The checked-in JSON artifact is regenerated with ``--write``; the test
``python/nebula/tests/test_wc_consumers.py`` fails if the artifact drifts from
this module. ``--census`` needs a reachable database and the live manifest
directory (``--manifest-dir``); structure checks and the drift test do not.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"
DEFAULT_MANIFEST_DIR = "state/promotion-flow"
DEFAULT_ROOT = Path(__file__).resolve().parents[2]

# ── Snapshot (live census, 2026-10-26; re-verified 2026-10-31) ────────────
# Every number is reproducible with ``--census``. Manifest values are a
# filesystem scan; all others are read-only SQL against the local nexus DB.
# Append-only counters (resolution_receipt, harvest_candidates_history) grow
# monotonically and are expected to exceed the snapshot; identity counters
# must not move.
SNAPSHOT: dict[str, Any] = {
    "captured_at": "2026-10-26T20:55:14Z",
    "reverified_at": "2026-10-31T09:27:06Z",
    "database": "nexus@localhost:5432",
    "manifest_dir": DEFAULT_MANIFEST_DIR,
    "counts": {
        "manifests": 190,
        "manifest_candidate_entries": 3800,
        "manifest_distinct_candidates": 788,
        "manifest_identity_pins": 190,
        "manifest_superseded": 141,
        "manifest_executed": 1,
        "manifest_promoted_flags": 21,
        "semantics_canonical_asset_total": 47987,
        "semantics_canonical_asset_active": 43091,
        "candidate_assets_active": 7456,
        "candidate_assets_with_canonical_key": 4086,
        "candidate_assets_without_canonical_key": 3370,
        "candidate_assets_orphan": 3372,
        "harvest_candidates": 11672,
        "harvest_candidates_history": 11675,
        "harvest_candidates_with_asset_id": 4084,
        "harvest_candidates_without_asset_id": 7588,
        "harvest_candidates_promoted": 21,
        "harvest_candidates_null_status": 11624,
        "requirements_total": 26,
        "requirements_with_candidate_id": 21,
        "requirements_linked_to_plan_0005": 21,
        "candidate_to_plan_crossrefs": 26,
        "promotion_batch": 0,
        "promotion_batch_candidate": 0,
        "resolution_canonical_asset": 13,
        "resolution_canonical_asset_with_key": 0,
        "resolution_receipt": 1891,
        "resolution_ticket": 0,
        "candidate_state_records": 4396,
        "candidate_embeddings": 753,
    },
    "runtime": {
        "promotion-execute.timer": "enabled/active; hourly at :45; last run 2026-10-26T20:45:55Z exit 0",
        "promotion-map.timer": "enabled/active; daily at 04:45",
        "promotion-shortlist.timer": "disabled/inactive",
        "live_promotion_processes": 0,
        "listening_consumers": [
            "nebula-srv:3101",
            "semantics-srv:3160",
            "conduit-mcp:3100",
            "tackle-mcp:3400",
            "assembly-srv:3107",
        ],
        "dormant_write_runtimes": [
            "moleculer/nebula",
            "adonisjs/nexus-control-edge",
            "/home/codex/dev/nexus-janitor",
        ],
    },
}

IDENTITY_MODEL: dict[str, Any] = {
    "candidate_id": {
        "surface": "nebula.harvest_candidates.id / nebula.harvest_candidates_history.id",
        "authority": "immutable uuid, assigned at harvest intake",
        "writer_fence": "F2",
    },
    "candidate_set_key": {
        "surface": "promotion_common.candidate_set_identity / resolution.candidate_set_key(uuid[], text)",
        "authority": "sha256 over the sorted candidate-id set (order-independent, duplicate-tolerant)",
        "writer_fence": "F1",
    },
    "asset_key": {
        "surface": "semantics.canonical_asset.canonical_asset_id",
        "authority": "asset:nexus:nebula_harvest_candidates:<candidate_id>; canonical_key is content, never identity",
        "writer_fence": "F3",
    },
    "requirement_id": {
        "surface": "nebula.requirements.id via requirements.candidate_id",
        "authority": "one requirement per promoted candidate; conduit_plan_id is lineage, not identity",
        "writer_fence": "F2",
    },
}

# ── Fences (AC3: per-consumer write-path isolation + atomic cutover) ──────
FENCES: dict[str, dict[str, Any]] = {
    "F0": {
        "title": "Read-only inventory fence",
        "rule": "The inventory may SELECT and scan files; it may never INSERT, UPDATE, DELETE or rewrite a manifest.",
        "enforcement": "read-only DSN (set_session(readonly=True)); no write branch in wc_consumers.py",
    },
    "F1": {
        "title": "Manifest single-writer adapter",
        "rule": "Every manifest write goes through promotion_common.save_manifest(); identity, batch_id and supersession fields are append-only.",
        "enforcement": "atomic temp-file + os.replace; one candidate_set_identity function; grep guard forbids direct open(...,'w') in promotion-flow",
        "cutover": "freeze timers -> checksum 190 manifests -> deploy adapter -> dry-run one batch -> verify parse + additive-only diff -> unfreeze",
    },
    "F2": {
        "title": "Candidate lifecycle single-writer RPC",
        "rule": "Status transitions go through nebula.set_candidate_status(); requirement creation goes through one spawn-requirement transaction that also performs the transition.",
        "enforcement": "SECURITY DEFINER RPC + column-level grants; alternate routes tombstoned; duplicate promote is an idempotent no-op; invalid transitions refused",
        "cutover": "freeze schedulers -> drain in-flight -> install RPC + grants -> replay idempotency and invalid-transition fixtures -> re-enable one writer",
    },
    "F3": {
        "title": "Canonical-asset mint fence",
        "rule": "One mint path owns semantics.canonical_asset; canonical_asset_id is identity and canonical_key is versioned content, never mutated in place.",
        "enforcement": "single mint function; canonical_asset_id convention enforced by CHECK/trigger; INSERT-only revisions; direct DML revoked from app roles",
        "cutover": "freeze asset writers -> reconcile candidate key space -> deploy mint function -> dual-write shadow -> parity check -> revoke direct DML -> unfreeze",
    },
    "F4": {
        "title": "Resolution mirror single-writer fence",
        "rule": "The W-A reconciliation ledger (V205) is the only writer of duplicate dispositions; V198 functions are the only writers of promotion_batch identity.",
        "enforcement": "append-only triggers; no parallel minting into resolution.canonical_asset; W-A3 disposition gates the mirror",
        "cutover": "apply V205 -> write dispositions via the DBA lane -> verify chain -> open the read mirror -> announce",
    },
    "F5": {
        "title": "Read-compatibility fence",
        "rule": "Readers bind to canonical_asset_id and immutable candidate ids; they never bind to a mutable UUID, a mutable canonical_key snapshot, or a retired legacy table.",
        "enforcement": "read-only DB role; compatibility views; contract tests that resolve the same identity pre/post; ``expired_at IS NULL`` pin",
        "cutover": "snapshot parity -> deploy readers behind a feature flag -> dual-read compare -> flip reads -> retire the old path",
    },
    "F6": {
        "title": "Process / lifecycle fence",
        "rule": "Exactly one live executor per promotion boundary; dormant runtimes cannot be restarted against production without passing the same gate.",
        "enforcement": "systemd single-instance + timer freeze window; flock in the adapter; alternate runtime tombstones; process census before/after",
        "cutover": "stop timers -> drain -> deploy -> prove zero competing processes -> start one timer -> soak",
    },
}

# ── Packages (AC2: one M-sized package per consumer boundary) ─────────────
PACKAGES: dict[str, dict[str, Any]] = {
    "W-C0": {
        "title": "Consumer inventory freeze + write-path census",
        "owner": "builder",
        "dependencies": [],
        "fence": "F0",
        "gate": "WC-D0",
        # W-C0 is the read-only inventory itself. Unlike every other package it
        # owns no runtime consumer; requiring one would force the inventory to
        # classify itself as a write-path boundary, which F0 forbids.
        "inventory_only": True,
        "evidence_outputs": [
            "docs/promotion-flow/w-c-consumer-inventory.json",
            "docs/promotion-flow/w-c-consumer-migration.md (§2 census)",
            "python/nebula/wc_consumers.py --census (live reproduction)",
        ],
        "stop_condition": (
            "Inventory accepted: every consumer of promotion identity is listed once, "
            "carries exactly one package id, and no live writer is unclassified. "
            "This is a non-authoritative stopping point — independent verification is "
            "the reviewer's, and later packages may add consumers by superseding this "
            "artifact, never by silently appending to a merged inventory."
        ),
        "state": "delivered by plan 8261665 (re-run of 8261655); verification pending",
    },
    "W-C1": {
        "title": "Manifest writer fence (state/promotion-flow)",
        "owner": "engineer",
        "dependencies": ["W-A1", "W-C8"],
        "fence": "F1",
        "gate": "WC-D1",
        "evidence_outputs": [
            "single-writer adapter conformance test",
            "atomic-write crash test (temp file never leaves a half manifest)",
            "190-manifest checksum parity report",
            "grep guard output: no direct open(...,'w') in python/promotion-flow",
        ],
        "stop_condition": (
            "Exactly one manifest write function exists; all 190 manifests parse; the "
            "checksum set is unchanged except for additive supersession/identity fields; "
            "and a second concurrent writer is refused by the adapter lease."
        ),
        "state": "not started",
    },
    "W-C2": {
        "title": "Candidate lifecycle writer fence (status + hierarchy + requirement)",
        "owner": "engineer",
        "dependencies": ["W-C8", "DBA roles"],
        "fence": "F2",
        "gate": "WC-D2",
        "evidence_outputs": [
            "writer census with every candidate-status write path and its fence status",
            "atomic spawn-requirement + transition test (no orphan requirement, no orphan promotion)",
            "idempotency test: second promote returns the current state",
            "invalid-transition refusal test",
            "21/21 promoted <-> requirement parity query",
        ],
        "stop_condition": (
            "One write path owns candidate status and requirement creation; every "
            "transition passes nebula.set_candidate_status; spawn + status are atomic; "
            "each of the 21 promoted candidates resolves to exactly one requirement; "
            "duplicate promotes are no-ops."
        ),
        "state": "not started",
    },
    "W-C3": {
        "title": "Canonical-asset mint fence (semantics.canonical_asset)",
        "owner": "dba",
        "dependencies": ["W-C2", "W-A3"],
        "fence": "F3",
        "gate": "WC-D3",
        "evidence_outputs": [
            "mint-path inventory + grant matrix",
            "candidate asset reconciliation ledger: 7,456 active assets, 3,372 orphans, 7,588 missing links",
            "canonical_key policy test (identity vs versioned content)",
            "duplicate active canonical_asset_id count (must be 0)",
        ],
        "stop_condition": (
            "One mint path exists; canonical_asset_id is the only identity; zero duplicate "
            "active keys; every orphan is dispositioned and every missing candidate link is "
            "backfilled or explicitly deferred with a reason; direct DML is revoked from app "
            "roles."
        ),
        "state": "not started",
    },
    "W-C4": {
        "title": "Resolution mirror + batch identity fence",
        "owner": "dba",
        "dependencies": ["W-A2", "W-A3", "W-A4", "W-A5", "W-A6", "W-A7", "V205", "V198"],
        "fence": "F4",
        "gate": "WC-D4",
        "evidence_outputs": [
            "resolution.canonical_asset disposition ledger (13 keyless rows)",
            "wr-repoint S3/S4 crosswalk proof",
            "V198 claim/concurrency test",
            "reader census: no live consumer treats the resolution mirror as authoritative",
        ],
        "stop_condition": (
            "All 13 keyless resolution assets are dispositioned by W-A3; the mirror has one "
            "ledger writer; promotion_batch identity is written only through V198 functions; "
            "and every reader is explicitly read-only or repointed."
        ),
        "state": "dependency-blocked on W-A ledger apply",
    },
    "W-C5": {
        "title": "Service read surfaces (semantics-srv, nebula-srv, MCP, control-edge, UI)",
        "owner": "engineer",
        "dependencies": ["W-C3", "W-B5", "W-B6"],
        "fence": "F5",
        "gate": "WC-D5",
        "evidence_outputs": [
            "consumer -> query matrix",
            "contract tests for asset envelope / identity claims / relations / revisions",
            "dual-read parity snapshot on the 21 promoted candidates plus a random sample",
            "projection hydration proof for the W-B surfaces",
        ],
        "stop_condition": (
            "Every read surface resolves promotion identity via canonical_asset_id; no reader "
            "depends on a mutable UUID or a retired surface; parity errors are zero on the "
            "promoted set and the sampled control set."
        ),
        "state": "not started",
    },
    "W-C6": {
        "title": "Offline and analytical readers",
        "owner": "builder",
        "dependencies": ["W-C3"],
        "fence": "F5",
        "gate": "WC-D6",
        "evidence_outputs": [
            "script inventory with read/write mode",
            "read-only DSN proof for every analytical script",
            "identity-resolution parity report",
            "embedding identity plan (candidate_index is not a candidate id)",
        ],
        "stop_condition": (
            "Every offline reader resolves identity through canonical_asset_id or is marked "
            "explicitly non-promotion; no script writes an identity surface; the embedding "
            "key gap is documented and either fixed or accepted with a reason."
        ),
        "state": "not started",
    },
    "W-C7": {
        "title": "Downstream lineage fence (requirements, plans, WRs, state, embeddings)",
        "owner": "engineer",
        "dependencies": ["W-C2", "architect lineage ruling"],
        "fence": "F2",
        "gate": "WC-D7",
        "evidence_outputs": [
            "lineage matrix: candidate -> requirement -> plan/WR -> state record -> embedding",
            "the 21 requirements all linked to plan 0005 (lineage defect register entry)",
            "26 candidate->plan cross-references dispositioned",
            "4,396 candidate state records and 753 embeddings keyed/re-derived",
        ],
        "stop_condition": (
            "Every promoted candidate resolves to exactly one requirement and a per-candidate "
            "plan/WR link or an explicit 'no plan' state; the five extra cross-references are "
            "dispositioned; state and embedding keys are documented and reproducible."
        ),
        "state": "not started",
    },
    "W-C8": {
        "title": "Scheduler and lifecycle ops fence",
        "owner": "engineer",
        "dependencies": ["DBA roles"],
        "fence": "F6",
        "gate": "WC-D8",
        "evidence_outputs": [
            "timer/process inventory (execute live, map live, shortlist disabled)",
            "freeze/drain transcript",
            "exactly-one-executor proof",
            "alternate-runtime tombstone or gate-parity proof",
            "7-day soak record",
        ],
        "stop_condition": (
            "Exactly one live executor per boundary; shortlist is explicitly disabled or "
            "fenced; alternate runtimes cannot write without the same gate; a freeze drill "
            "passes and the soak window is green."
        ),
        "state": "prerequisite for W-C1/W-C2 cutover",
    },
}

# ── Consumers (AC1) ───────────────────────────────────────────────────────
# mode: writer | reader | writer+reader | scheduler | data
# runtime: live | dormant | offline | manual | future
CONSUMERS: list[dict[str, Any]] = [
    # W-C1 — manifest boundary
    {
        "id": "C-MAN-01",
        "name": "Promotion-flow shared adapter",
        "path": "python/promotion-flow/promotion_common.py",
        "package": "W-C1",
        "mode": "writer+reader",
        "surface": "manifest",
        "runtime": "live",
        "identity_use": "candidate_set_identity(); save_manifest(); load_manifests(); open_batch_covering_set(); mark_superseded()",
        "evidence": "single adapter used by every stage; direct file writes are the fence target",
        "notes": "F1 boundary itself. Must gain atomic replace + a writer lease.",
    },
    {
        "id": "C-MAN-02",
        "name": "Stage 1 shortlist",
        "path": "python/promotion-flow/stage1_shortlist.py",
        "package": "W-C1",
        "mode": "writer+reader",
        "surface": "manifest",
        "runtime": "dormant",
        "identity_use": "emits a batch only when open_batch_covering_set() finds no open twin",
        "evidence": "promotion-shortlist.timer is disabled; /tmp/promotion-shortlist.log is empty",
        "notes": "Dormant writer — a restart re-introduces a second emitter unless F1 lands first.",
    },
    {
        "id": "C-MAN-03",
        "name": "Stage 3 gated executor",
        "path": "python/promotion-flow/stage3_execute.py",
        "package": "W-C1",
        "mode": "writer+reader",
        "surface": "manifest",
        "runtime": "live",
        "identity_use": "loads manifests, stamps executed flags and per-candidate promoted state",
        "evidence": "promotion-execute.timer last ran 2026-10-26T20:45:55Z, exit 0",
        "notes": "Primary live manifest writer; also crosses W-C2 (status/requirement).",
        "also_touches": ["W-C2", "W-C7"],
    },
    {
        "id": "C-MAN-04",
        "name": "Duplicate reconciliation (ST.02)",
        "path": "python/promotion-flow/reconcile_duplicates.py",
        "package": "W-C1",
        "mode": "writer",
        "surface": "manifest",
        "runtime": "manual",
        "identity_use": "groups by candidate_set_identity and appends supersession markers",
        "evidence": "141 of 190 manifests carry superseded_by",
        "notes": "Append-only marker writer; must remain the only supersession writer.",
    },
    {
        "id": "C-MAN-05",
        "name": "Promotion identity pin (W6.04)",
        "path": "python/promotion-flow/pin_promotion_identity.py",
        "package": "W-C1",
        "mode": "writer",
        "surface": "manifest",
        "runtime": "manual",
        "identity_use": "appends contract/evaluator/law identity_pin to every manifest",
        "evidence": "190/190 manifests carry identity_pin",
        "notes": "Append-only pin writer; a second pin implementation would fork identity.",
    },
    {
        "id": "C-MAN-06",
        "name": "Card re-emit",
        "path": "python/promotion-flow/reemit_cards.py",
        "package": "W-C1",
        "mode": "writer",
        "surface": "manifest",
        "runtime": "manual",
        "identity_use": "renders cards by 8-char candidate id and saves the manifest",
        "evidence": "card short id = candidate id[:8] (CARD_SPEC.md)",
        "notes": "Short id is display-only; the fence forbids promoting on a short id.",
    },
    {
        "id": "C-MAN-07",
        "name": "W-A inventory collector",
        "path": "python/nebula/collect.py",
        "package": "W-C1",
        "mode": "reader",
        "surface": "manifest",
        "runtime": "manual",
        "identity_use": "reads batch-*.json and groups by candidate_set_identity",
        "evidence": "W-A census: 190 manifests, 0 undecodable",
        "notes": "Read-only; must keep reading pre-fence and post-fence shapes.",
    },
    {
        "id": "C-MAN-08",
        "name": "W-A duplicate-history detector",
        "path": "python/nebula/duplicate_history.py",
        "package": "W-C1",
        "mode": "reader",
        "surface": "manifest",
        "runtime": "manual",
        "identity_use": "manifest_sets() over the candidate-set identity",
        "evidence": "W-A1: 10 duplicate sets / 151 members",
        "notes": "Read-only; the W-A ledger consumes the same identity.",
    },
    {
        "id": "C-MAN-09",
        "name": "Manifest sediment",
        "path": "state/promotion-flow/batch-*.json",
        "package": "W-C1",
        "mode": "data",
        "surface": "manifest",
        "runtime": "live",
        "identity_use": "190 files / 3,800 candidate entries / 788 distinct candidates",
        "evidence": "all 190 carry identity_pin; 141 superseded; 1 executed; 21 promoted flags",
        "notes": "Preserved append-only; never deleted or rewritten by W-C.",
    },
    # W-C2 — candidate lifecycle boundary
    {
        "id": "C-CAN-01",
        "name": "Stage 3 promote()",
        "path": "python/promotion-flow/stage3_execute.py::promote",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "live",
        "identity_use": "POST spawn-requirement then PATCH status='promoted'",
        "evidence": "two requests, not one transaction; status PATCH bypasses set_candidate_status",
        "notes": "Atomicity defect WCF-02.",
    },
    {
        "id": "C-CAN-02",
        "name": "Stage 0 hierarchy mapper",
        "path": "python/promotion-flow/stage0_map.py",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "live",
        "identity_use": "PATCHes system_id/subsystem_id above the 0.80 confidence bar",
        "evidence": "promotion-map.timer enabled, daily 04:45",
        "notes": "Mapping is part of the identity frame (Gap C); must not change candidate id.",
    },
    {
        "id": "C-CAN-03",
        "name": "Nebula spawn-requirement route",
        "path": "typescript/nebula-srv/src/routes.ts (POST /api/harvest-candidates/:id/spawn-requirement)",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "live",
        "identity_use": "links hierarchy and INSERTs requirements.candidate_id in one transaction",
        "evidence": "31 rows total, 21 with candidate_id; 0 duplicate candidate links",
        "notes": "Does not transition candidate status — the split behind WCF-02.",
        "also_touches": ["W-C7"],
    },
    {
        "id": "C-CAN-04",
        "name": "Nebula candidate PATCH route",
        "path": "typescript/nebula-srv/src/routes.ts (PATCH /api/harvest-candidates/:id)",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "live",
        "identity_use": "direct UPDATE of status, hierarchy, work_request_id and placement fields",
        "evidence": "used by stage3 and MCP update tool",
        "notes": "Bypasses nebula.set_candidate_status — WCF-01.",
    },
    {
        "id": "C-CAN-05",
        "name": "Nebula promote route",
        "path": "typescript/nebula-srv/src/routes.ts (POST /api/harvest-candidates/:id/promote)",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "live",
        "identity_use": "calls nebula.set_candidate_status(id, status)",
        "evidence": "the only route using the validated transition function",
        "notes": "Canonical target for F2; accepts useful|rejected|promoted.",
    },
    {
        "id": "C-CAN-06",
        "name": "Nebula MCP candidate tools",
        "path": "typescript/nebula-mcp/src/tools/index.ts + api/nebulaClient.ts",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "live",
        "identity_use": "nebula_update_harvest_candidate, nebula_spawn_requirement_from_candidate",
        "evidence": "MCP tools proxy the REST routes above",
        "notes": "Must not expose the bypassing PATCH path once F2 lands.",
    },
    {
        "id": "C-CAN-07",
        "name": "Control-edge candidate writers",
        "path": "adonisjs/nexus-control-edge/app/controllers/nebula_harvest_controller.ts + nebula_meta_controller.ts",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "dormant",
        "identity_use": "promoteCandidate (procedure), cpfPromote (direct UPDATE), updateHarvestCandidate",
        "evidence": "no control-edge listener at census time",
        "notes": "Deployable bypass — must be tombstoned or pass F2 before restart.",
    },
    {
        "id": "C-CAN-08",
        "name": "Moleculer nebula candidate writers",
        "path": "moleculer/nebula/src/routes.ts",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "dormant",
        "identity_use": "duplicate PATCH/promote/spawn-requirement implementation",
        "evidence": "moleculer nebula is not listening",
        "notes": "Alternate runtime with a full competing write surface (WCF-09).",
    },
    {
        "id": "C-CAN-09",
        "name": "Tackle candidate promoter",
        "path": "python/tackle/candidate_promote.py",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "manual",
        "identity_use": "direct SQL UPDATE status='promoted' (no procedure)",
        "evidence": "bypasses the transition function entirely",
        "notes": "WCF-01; also emits candidate.promoted cascade.",
    },
    {
        "id": "C-CAN-10",
        "name": "Tackle planner greenlight",
        "path": "python/tackle/planner.py",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "manual",
        "identity_use": "direct SQL UPDATE status='promoted' during greenlight",
        "evidence": "bypasses the transition function entirely",
        "notes": "WCF-01.",
    },
    {
        "id": "C-CAN-11",
        "name": "Candidate status transition function",
        "path": "nebula.set_candidate_status(uuid, text)",
        "package": "W-C2",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "live",
        "identity_use": "validates pending -> linked|useful|rejected, linked -> useful|rejected, useful -> promoted|rejected",
        "evidence": "present in the live DB and sql/ci-bootstrap/nexus-ci-bootstrap.sql; no versioned V-migration",
        "notes": "Canonical target for F2; WCF-11 (function is unversioned).",
    },
    # W-C3 — asset mint boundary
    {
        "id": "C-AST-01",
        "name": "Candidate asset backfill",
        "path": "bin/candidate-asset-backfill.py",
        "package": "W-C3",
        "mode": "writer",
        "surface": "asset",
        "runtime": "manual",
        "identity_use": "mints asset:nexus:nebula_harvest_candidates:<id> + rev:1 and sets asset_id",
        "evidence": "7,456 candidate assets; 3,372 orphans; 3,370 without canonical_key",
        "notes": "INSERT ... DO NOTHING mint policy; must become the single mint path or delegate to it.",
    },
    {
        "id": "C-AST-02",
        "name": "Agent-record asset backfill",
        "path": "bin/canonical-asset-backfill-agent-records.py",
        "package": "W-C3",
        "mode": "writer",
        "surface": "asset",
        "runtime": "manual",
        "identity_use": "mints agent_record:<kind> assets with their own namespace",
        "evidence": "7,492 agent_record:report assets active",
        "notes": "Same table, different namespace; the mint function must be namespace-safe.",
    },
    {
        "id": "C-AST-03",
        "name": "Voyager adapter mint",
        "path": "python/voyager-adapter/db.py",
        "package": "W-C3",
        "mode": "writer",
        "surface": "asset",
        "runtime": "offline",
        "identity_use": "ON CONFLICT DO UPDATE SET canonical_key = EXCLUDED.canonical_key",
        "evidence": "in-place canonical_key mutation contradicts the revision model",
        "notes": "WCF-06; highest-risk second mint path.",
    },
    {
        "id": "C-AST-04",
        "name": "Voyager semantics adapter mint",
        "path": "python/voyager/src/voyager_semantics_adapter.py",
        "package": "W-C3",
        "mode": "writer",
        "surface": "asset",
        "runtime": "offline",
        "identity_use": "upsert_canonical_asset DO UPDATE key/hashes + validity_start=now()",
        "evidence": "second in-place upsert implementation",
        "notes": "WCF-06.",
    },
    {
        "id": "C-AST-05",
        "name": "Knowledge-graph asset backfill",
        "path": "bin/import-knowledge-graph.sh + sql/V083__graph_entities_asset_id_backfill.sql",
        "package": "W-C3",
        "mode": "writer",
        "surface": "asset",
        "runtime": "manual",
        "identity_use": "mints/updates knowledge_entity assets",
        "evidence": "7,118 knowledge_entity assets, 2,380 active",
        "notes": "Same table; must share the mint function and expiry discipline.",
    },
    {
        "id": "C-AST-06",
        "name": "Semantics-srv canonical-asset CRUD",
        "path": "typescript/semantics-srv/src/routes/semantics.ts",
        "package": "W-C3",
        "mode": "writer+reader",
        "surface": "asset",
        "runtime": "live",
        "identity_use": "generic create/append-only replace/expire on canonical_asset",
        "evidence": "listener :3160",
        "notes": "Exposes generic writes; F3 must constrain the table allow-list and key shape.",
    },
    {
        "id": "C-AST-07",
        "name": "Moleculer semantics CRUD",
        "path": "moleculer/semantics/services/*.ts",
        "package": "W-C3",
        "mode": "writer+reader",
        "surface": "asset",
        "runtime": "dormant",
        "identity_use": "same canonical-asset CRUD surfaces as semantics-srv",
        "evidence": "not listening at census time",
        "notes": "Alternate runtime bypass.",
    },
    {
        "id": "C-AST-08",
        "name": "System external-id backfill",
        "path": "bin/populate_system_external_ids.sql",
        "package": "W-C3",
        "mode": "writer",
        "surface": "asset",
        "runtime": "manual",
        "identity_use": "reads canonical_asset and writes asset_relation external ids",
        "evidence": "relations point at canonical asset ids",
        "notes": "Must not create assets; relation-only writer.",
    },
    {
        "id": "C-AST-09",
        "name": "Canonical asset table",
        "path": "semantics.canonical_asset",
        "package": "W-C3",
        "mode": "data",
        "surface": "asset",
        "runtime": "live",
        "identity_use": "47,987 total / 43,091 active; 7,456 candidate assets",
        "evidence": "only pguser holds DML grants",
        "notes": "WCF-03 (no app roles), WCF-04 (orphans/keys).",
    },
    {
        "id": "C-AST-10",
        "name": "Asset revision table",
        "path": "semantics.asset_revision",
        "package": "W-C3",
        "mode": "data",
        "surface": "asset",
        "runtime": "live",
        "identity_use": "revision_id / content_hash / source_hash lineage per asset",
        "evidence": "candidate backfill writes <asset_key>:rev:1",
        "notes": "F3 requires append-only revisions, never an in-place key update.",
    },
    # W-C4 — resolution mirror boundary
    {
        "id": "C-RES-01",
        "name": "W-A canonical-asset collector",
        "path": "python/nebula/collect.py::collect_canonical_assets",
        "package": "W-C4",
        "mode": "reader",
        "surface": "resolution",
        "runtime": "manual",
        "identity_use": "reads resolution.canonical_asset.canonical_key",
        "evidence": "13 rows, 0 with canonical_key (W-A3 unidentified)",
        "notes": "Read-only; blocked on W-A3 for any mirror promotion.",
    },
    {
        "id": "C-RES-02",
        "name": "W-A canonical-asset detector",
        "path": "python/nebula/duplicate_history.py::canonical_asset_sets",
        "package": "W-C4",
        "mode": "reader",
        "surface": "resolution",
        "runtime": "manual",
        "identity_use": "groups resolution.canonical_asset by canonical_key",
        "evidence": "W-A3: 1 set / 13 members",
        "notes": "Read-only.",
    },
    {
        "id": "C-RES-03",
        "name": "WR repoint verifier",
        "path": "bin/wr-repoint-verify.py",
        "package": "W-C4",
        "mode": "reader",
        "surface": "resolution",
        "runtime": "manual",
        "identity_use": "S3/S4 crosswalk: asset:nexus:vision_work_requests:<id>",
        "evidence": "mirror key tail must equal the canonical PK",
        "notes": "Read-only verifier; the only executable crosswalk proof today.",
    },
    {
        "id": "C-RES-04",
        "name": "Aegis invoke-log FK",
        "path": "python/aegis/invocations.sql",
        "package": "W-C4",
        "mode": "writer",
        "surface": "resolution",
        "runtime": "manual",
        "identity_use": "sol_script.invoke_log.entity_id REFERENCES resolution.canonical_asset(id)",
        "evidence": "FK writer couples invocation history to the mirror",
        "notes": "Mirror retirement cannot drop rows without addressing this FK.",
    },
    {
        "id": "C-RES-05",
        "name": "ST.01 batch identity functions",
        "path": "sql/V198__st01_promotion_batch_candidate_set_keys.sql",
        "package": "W-C4",
        "mode": "writer",
        "surface": "resolution",
        "runtime": "future",
        "identity_use": "candidate_set_key(), eligibility_hash(), claim_open_batch()",
        "evidence": "resolution.promotion_batch 0 rows; zero live consumers",
        "notes": "Inert-until-used; F4 keeps it the only batch-identity writer.",
    },
    {
        "id": "C-RES-06",
        "name": "Resolution canonical-asset mirror",
        "path": "resolution.canonical_asset",
        "package": "W-C4",
        "mode": "data",
        "surface": "resolution",
        "runtime": "live",
        "identity_use": "13 rows, all canonical_key NULL",
        "evidence": "W-A3 unidentified set",
        "notes": "Not authoritative until W-A3 dispositions land.",
    },
    # W-C5 — service read surfaces
    {
        "id": "C-RD-01",
        "name": "Nebula-srv asset joins",
        "path": "typescript/nebula-srv/src/routes.ts",
        "package": "W-C5",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "live",
        "identity_use": "JOIN semantics.canonical_asset ON ca.id = ar.to_asset_id AND ca.expired_at IS NULL",
        "evidence": "listener :3101; four join sites",
        "notes": "Must pin canonical_asset_id, not the mutable uuid, in projections.",
    },
    {
        "id": "C-RD-02",
        "name": "Moleculer nebula asset joins",
        "path": "moleculer/nebula/src/routes.ts",
        "package": "W-C5",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "dormant",
        "identity_use": "duplicate of the nebula-srv joins",
        "evidence": "not listening",
        "notes": "Alternate read surface; must be repointed or tombstoned.",
    },
    {
        "id": "C-RD-03",
        "name": "Semantics-srv read routes",
        "path": "typescript/semantics-srv/src/routes/semantics.ts",
        "package": "W-C5",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "live",
        "identity_use": "asset envelope, identity claims, relations, revisions, external ids",
        "evidence": "listener :3160",
        "notes": "Returns both uuid and canonicalAssetId; callers must prefer the latter.",
    },
    {
        "id": "C-RD-04",
        "name": "Moleculer semantics read handlers",
        "path": "moleculer/semantics/services/handlers.ts",
        "package": "W-C5",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "dormant",
        "identity_use": "same asset envelopes",
        "evidence": "not listening",
        "notes": "Alternate read surface.",
    },
    {
        "id": "C-RD-05",
        "name": "Control-edge semantics/meta controllers",
        "path": "adonisjs/nexus-control-edge/app/controllers/semantics_controller.ts + nebula_meta_controller.ts",
        "package": "W-C5",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "dormant",
        "identity_use": "control-plane asset/meta projections",
        "evidence": "no listener at census time",
        "notes": "Must not be restarted on legacy read paths.",
    },
    {
        "id": "C-RD-06",
        "name": "Nebula MCP read tools",
        "path": "typescript/nebula-mcp/src/tools/index.ts",
        "package": "W-C5",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "live",
        "identity_use": "harvest candidate / requirement / system reads",
        "evidence": "nebula-mcp-sse :3102 is live",
        "notes": "Contract tests must resolve the same candidate->requirement identity.",
    },
    {
        "id": "C-RD-07",
        "name": "Conduit steward recall",
        "path": "typescript/conduit-mcp/src/steward-recall.ts",
        "package": "W-C5",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "live",
        "identity_use": "reads evidence links keyed by nebula_candidate_id",
        "evidence": "conduit-mcp :3100 is live",
        "notes": "Candidate id is the join key; a repoint must preserve it.",
    },
    {
        "id": "C-RD-08",
        "name": "Vision / duality UI promotion cards",
        "path": "angular/vision-ui + angular/duality-ui",
        "package": "W-C5",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "dormant",
        "identity_use": "renders manifest cards and candidate status",
        "evidence": "no promotion UI listener checked; cards are read-only",
        "notes": "Display-only short ids must never be used as write keys.",
    },
    # W-C6 — offline/analytical readers
    {
        "id": "C-OFF-01",
        "name": "Candidate state record seeder",
        "path": "bin/seed-candidate-state-records.py",
        "package": "W-C6",
        "mode": "writer+reader",
        "surface": "read_model",
        "runtime": "manual",
        "identity_use": "reads canonical_asset by candidate asset key and writes shrapnel asset_id members",
        "evidence": "4,396 candidate state records",
        "notes": "Writes only state records; must not mint assets.",
    },
    {
        "id": "C-OFF-02",
        "name": "Mesh reconcile",
        "path": "bin/mesh-reconcile.py",
        "package": "W-C6",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "manual",
        "identity_use": "JOIN semantics.canonical_asset for service asset identity",
        "evidence": "read-only reconciliation query",
        "notes": "Non-promotion but shares the asset table.",
    },
    {
        "id": "C-OFF-03",
        "name": "Epistemologist observation reader",
        "path": "python/epistemologist/db.py",
        "package": "W-C6",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "offline",
        "identity_use": "joins source_observation -> asset_revision -> canonical_asset",
        "evidence": "asset_kind filter for transcripts",
        "notes": "Shared-surface regression target for F3.",
    },
    {
        "id": "C-OFF-04",
        "name": "Auditor observation reader",
        "path": "python/auditor/db.py",
        "package": "W-C6",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "offline",
        "identity_use": "same observation join, asset_kind='transcript'",
        "evidence": "read-only",
        "notes": "Shared-surface regression target.",
    },
    {
        "id": "C-OFF-05",
        "name": "Semantic-layer embedder",
        "path": "bin/embed_semantic_layers.py",
        "package": "W-C6",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "manual",
        "identity_use": "joins source_observation -> asset_revision -> canonical_asset",
        "evidence": "read-only",
        "notes": "Shared-surface regression target.",
    },
    {
        "id": "C-OFF-06",
        "name": "Promotion readiness gate",
        "path": "python/promotion-flow/promotion_gate.py",
        "package": "W-C6",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "live",
        "identity_use": "reads candidate status, CPF and open planner questions",
        "evidence": "imported by stage3",
        "notes": "Must consume the same canonical status vocabulary after W-C2.",
    },
    {
        "id": "C-OFF-07",
        "name": "Candidate embedding batch",
        "path": "bin/batch_embed_and_match.py",
        "package": "W-C6",
        "mode": "writer+reader",
        "surface": "read_model",
        "runtime": "manual",
        "identity_use": "writes harvest_candidate_embeddings keyed by harvest_id + candidate_index",
        "evidence": "753 embedding rows; no candidate_id column",
        "notes": "WCF-10: embedding key is not a candidate id.",
    },
    {
        "id": "C-OFF-08",
        "name": "Unified semantic search",
        "path": "bin/unified_semantic_search.py",
        "package": "W-C6",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "live",
        "identity_use": "reads harvest_candidate_embeddings as the harvested layer",
        "evidence": "exposed through knowledge-mcp",
        "notes": "Must keep results traceable to candidate id, not just title.",
    },
    {
        "id": "C-OFF-09",
        "name": "Cross-schema classifier",
        "path": "bin/cross_schema_classifier.py",
        "package": "W-C6",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "offline",
        "identity_use": "reads candidate embeddings",
        "evidence": "read-only",
        "notes": "Non-promotion analytical reader.",
    },
    {
        "id": "C-OFF-10",
        "name": "Cross-schema rematcher",
        "path": "bin/batch_rematch_cross_schema.py",
        "package": "W-C6",
        "mode": "reader",
        "surface": "read_model",
        "runtime": "offline",
        "identity_use": "joins harvest_candidate_embeddings",
        "evidence": "read-only",
        "notes": "Non-promotion analytical reader.",
    },
    # W-C7 — downstream lineage
    {
        "id": "C-DWN-01",
        "name": "Requirements candidate link",
        "path": "nebula.requirements.candidate_id",
        "package": "W-C7",
        "mode": "data",
        "surface": "downstream",
        "runtime": "live",
        "identity_use": "21 requirements carry a candidate_id; 5 requirements are uncoupled",
        "evidence": "0 duplicate candidate links; 0 promoted candidates without a requirement",
        "notes": "This is the surviving promotion lineage column.",
    },
    {
        "id": "C-DWN-02",
        "name": "Candidate -> plan cross-references",
        "path": "nebula.cross_references (rel_type='ag:spawns_plan')",
        "package": "W-C7",
        "mode": "data",
        "surface": "downstream",
        "runtime": "live",
        "identity_use": "26 candidate->plan edges vs 21 promoted candidates",
        "evidence": "5 extra edges are legacy/duplicate lineage (WCF-12)",
        "notes": "The 5 extras need an explicit disposition, not deletion.",
    },
    {
        "id": "C-DWN-03",
        "name": "Requirement -> plan lineage",
        "path": "nebula.requirements.conduit_plan_id",
        "package": "W-C7",
        "mode": "data",
        "surface": "downstream",
        "runtime": "live",
        "identity_use": "all 21 promoted-candidate requirements link to plan 0005",
        "evidence": "stage3 payload hard-codes planRef='0005'",
        "notes": "WCF-07: per-candidate lineage is absent, so plan identity is generic.",
    },
    {
        "id": "C-DWN-04",
        "name": "Conduit plans / WorkRequests",
        "path": "conduit plan pipeline",
        "package": "W-C7",
        "mode": "reader",
        "surface": "downstream",
        "runtime": "live",
        "identity_use": "consumes requirement lineage; no candidate id in conduit plan identity",
        "evidence": "plan 0005 is the promotion-flow plan",
        "notes": "A per-candidate WR link is the lineage target of W-C7.",
    },
    {
        "id": "C-DWN-05",
        "name": "Shrapnel candidate state records",
        "path": "shrapnel.object_instance (asset_id member)",
        "package": "W-C7",
        "mode": "data",
        "surface": "downstream",
        "runtime": "live",
        "identity_use": "4,396 records keyed by the canonical asset id",
        "evidence": "seeded by bin/seed-candidate-state-records.py",
        "notes": "Downstream identity is already the asset key, not the candidate uuid.",
    },
    {
        "id": "C-DWN-06",
        "name": "Harvest candidate embeddings",
        "path": "nebula.harvest_candidate_embeddings",
        "package": "W-C7",
        "mode": "data",
        "surface": "downstream",
        "runtime": "live",
        "identity_use": "753 rows keyed by harvest_id + candidate_index, not candidate_id",
        "evidence": "no candidate_id column (WCF-10)",
        "notes": "Needs a candidate-id key or a documented join recipe.",
    },
    {
        "id": "C-DWN-07",
        "name": "Assembly promotion threads/cards",
        "path": "assembly-srv forums (planning, harvest-candidates)",
        "package": "W-C7",
        "mode": "reader",
        "surface": "downstream",
        "runtime": "live",
        "identity_use": "batch thread title [promotion-batch <id>] and 8-char card ids",
        "evidence": "assembly-srv :3107 live",
        "notes": "Human-facing projection; display ids are not write keys.",
    },
    {
        "id": "C-DWN-08",
        "name": "Tackle requirement promoter",
        "path": "python/tackle/intent_requirement_promote.py",
        "package": "W-C7",
        "mode": "writer+reader",
        "surface": "downstream",
        "runtime": "manual",
        "identity_use": "reads promoted candidates, creates requirements, updates candidate status",
        "evidence": "checks for an existing requirement before insert",
        "notes": "Competing requirement-creation path; must converge on F2 or retire.",
        "also_touches": ["W-C2"],
    },
    # W-C8 — scheduler / lifecycle
    {
        "id": "C-OPS-01",
        "name": "Promotion executor timer",
        "path": "config/systemd/user/promotion-execute.{timer,service}",
        "package": "W-C8",
        "mode": "scheduler",
        "surface": "manifest",
        "runtime": "live",
        "identity_use": "hourly stage3 run; the only live promotion executor",
        "evidence": "enabled/active; last run 2026-10-26T20:45:55Z, exit 0",
        "notes": "Cutover freeze window target.",
    },
    {
        "id": "C-OPS-02",
        "name": "Promotion mapper timer",
        "path": "config/systemd/user/promotion-map.{timer,service}",
        "package": "W-C8",
        "mode": "scheduler",
        "surface": "candidate_row",
        "runtime": "live",
        "identity_use": "daily stage0 hierarchy mapping",
        "evidence": "enabled/active; daily 04:45",
        "notes": "Must be frozen during the W-C2 cutover.",
    },
    {
        "id": "C-OPS-03",
        "name": "Promotion shortlist timer",
        "path": "config/systemd/user/promotion-shortlist.{timer,service}",
        "package": "W-C8",
        "mode": "scheduler",
        "surface": "manifest",
        "runtime": "dormant",
        "identity_use": "hourly stage1 batch emission",
        "evidence": "disabled/inactive",
        "notes": "Explicitly disabled; must stay disabled until F1 lands.",
    },
    {
        "id": "C-OPS-04",
        "name": "Nexus-janitor clone",
        "path": "/home/codex/dev/nexus-janitor",
        "package": "W-C8",
        "mode": "writer+reader",
        "surface": "manifest",
        "runtime": "dormant",
        "identity_use": "full second copy of promotion-flow on branch main",
        "evidence": "no process; shares the same state-dir convention",
        "notes": "A restart from this tree would be an uncontrolled writer (WCF-09).",
    },
    {
        "id": "C-OPS-05",
        "name": "Out-of-band psql / DBA scripts",
        "path": "manual SQL",
        "package": "W-C8",
        "mode": "writer",
        "surface": "candidate_row",
        "runtime": "manual",
        "identity_use": "direct DML as pguser",
        "evidence": "pguser holds INSERT/UPDATE/DELETE/TRUNCATE on every target table",
        "notes": "WCF-03: zero direct writers is unprovable until app roles exist.",
    },
    {
        "id": "C-OPS-06",
        "name": "Promotion logs",
        "path": "/tmp/promotion-{map,shortlist,execute}.log",
        "package": "W-C8",
        "mode": "reader",
        "surface": "manifest",
        "runtime": "live",
        "identity_use": "execution evidence and drain proof",
        "evidence": "execute log shows hourly skip/no-op passes",
        "notes": "Evidence sink; not a control surface.",
    },
]

# ── Stage D gates (AC4) ───────────────────────────────────────────────────
# Every gate carries the same three checklist families: read-path validation,
# write-path validation, rollback plan. DB-layer gates require the third
# signatory (dba); non-DB gates carry a recorded DBA waiver.
GATES: dict[str, dict[str, Any]] = {
    "WC-D0": {
        "package": "W-C0",
        "db_layer": False,
        "read_path": ["inventory reproduction (`--census`) matches every snapshot number"],
        "write_path": ["no write branch exists; process runs with a read-only DB session"],
        "rollback": ["delete the regenerated artifact; no canonical state was touched"],
        "integrity": ["every consumer has exactly one package; inventory is deterministic"],
        "evidence_slot": "WC0-INVENTORY",
        "signatories": ["operator", "architect"],
        "dba_waiver": True,
    },
    "WC-D1": {
        "package": "W-C1",
        "db_layer": False,
        "read_path": [
            "all 190 manifests parse before and after the fence",
            "W-A inventory reproduces the same 10 W-A1 sets",
        ],
        "write_path": [
            "one save_manifest() implementation; grep guard shows no direct open(...,'w')",
            "concurrent writer is refused by the adapter lease",
            "atomic replace leaves no partial manifest after a kill -9 drill",
        ],
        "rollback": [
            "redeploy the previous promotion-flow commit; manifests are additive-only",
            "restore the timer schedule; no manifest was deleted",
        ],
        "integrity": ["checksum parity except additive identity/supersession fields"],
        "evidence_slot": "WC1-MANIFEST-FENCE",
        "signatories": ["operator", "architect"],
        "dba_waiver": True,
    },
    "WC-D2": {
        "package": "W-C2",
        "db_layer": True,
        "read_path": [
            "GET candidate and GET requirement resolve the same identity for all 21 promoted candidates",
            "invalid transitions are refused with the same reason before and after",
        ],
        "write_path": [
            "exactly one status writer; every transition calls nebula.set_candidate_status",
            "spawn-requirement and status transition commit in one transaction",
            "duplicate promote is an idempotent no-op",
            "direct UPDATE from app roles is refused by grant/freeze",
        ],
        "rollback": [
            "re-grant the app role UPDATE for the drain window and restore the previous route",
            "no row is deleted; the transition audit remains append-only",
        ],
        "integrity": [
            "21/21 promoted <-> requirement parity",
            "0 promoted candidates without exactly one requirement",
        ],
        "evidence_slot": "WC2-LIFECYCLE-FENCE",
        "signatories": ["operator", "architect", "dba"],
        "dba_waiver": False,
    },
    "WC-D3": {
        "package": "W-C3",
        "db_layer": True,
        "read_path": [
            "canonical_asset_id resolves for the 7,456 candidate assets",
            "asset revisions resolve content for a sampled set",
        ],
        "write_path": [
            "one mint function; no direct INSERT/UPDATE/DELETE outside it",
            "canonical_key is INSERT-only per revision, never in-place",
            "app roles cannot write the table directly",
        ],
        "rollback": [
            "re-grant the previous writer and restore the prior mint function",
            "new assets are expired, never deleted",
        ],
        "integrity": [
            "0 duplicate active canonical_asset_id",
            "3,372 orphans dispositioned; 7,588 missing links backfilled or explicitly deferred",
        ],
        "evidence_slot": "WC3-ASSET-FENCE",
        "signatories": ["operator", "architect", "dba"],
        "dba_waiver": False,
    },
    "WC-D4": {
        "package": "W-C4",
        "db_layer": True,
        "read_path": [
            "wr-repoint S3/S4 crosswalk passes",
            "no live consumer reads the resolution mirror as authoritative",
        ],
        "write_path": [
            "V205 is the only disposition writer; V198 functions are the only batch writer",
            "append-only triggers reject UPDATE/DELETE",
        ],
        "rollback": [
            "drop the empty ledger surfaces per the W-A rollback sketch",
            "canonical history is untouched",
        ],
        "integrity": [
            "13 keyless resolution assets dispositioned",
            "promotion_batch writers = V198 functions only",
        ],
        "evidence_slot": "WC4-RESOLUTION-FENCE",
        "signatories": ["operator", "architect", "dba"],
        "dba_waiver": False,
    },
    "WC-D5": {
        "package": "W-C5",
        "db_layer": False,
        "read_path": [
            "dual-read parity on the 21 promoted set plus a random control sample",
            "every surface returns canonical_asset_id and candidate id together",
        ],
        "write_path": [
            "no read surface writes an identity table",
            "read-only role cannot INSERT/UPDATE/DELETE",
        ],
        "rollback": [
            "flip the read feature flag back to the previous path",
            "no data migration is required to roll back",
        ],
        "integrity": ["parity errors = 0; no reader depends on a mutable uuid"],
        "evidence_slot": "WC5-READ-PARITY",
        "signatories": ["operator", "architect"],
        "dba_waiver": True,
    },
    "WC-D6": {
        "package": "W-C6",
        "db_layer": False,
        "read_path": [
            "each script resolves identity through canonical_asset_id or is marked non-promotion",
            "sampled analytical output matches the canonical census",
        ],
        "write_path": [
            "analytical scripts use read-only DSNs",
            "only the state seeder writes, and only to shrapnel state records",
        ],
        "rollback": [
            "restore the previous script version; no identity surface was modified",
        ],
        "integrity": ["0 analytical writes to identity tables; embedding key gap documented"],
        "evidence_slot": "WC6-OFFLINE-PARITY",
        "signatories": ["operator", "architect"],
        "dba_waiver": True,
    },
    "WC-D7": {
        "package": "W-C7",
        "db_layer": False,
        "read_path": [
            "candidate -> requirement -> plan/WR resolves for all 21 promoted candidates",
            "state records and embeddings resolve to the same candidate id",
        ],
        "write_path": [
            "requirement creation uses the W-C2 transaction only",
            "no second requirement-creation path remains writable",
        ],
        "rollback": [
            "repoint to the prior lineage writer; requirements and links are additive",
        ],
        "integrity": [
            "26 cross-reference edges dispositioned (21 canonical + 5 legacy)",
            "per-candidate lineage present or explicitly 'no plan'",
        ],
        "evidence_slot": "WC7-LINEAGE",
        "signatories": ["operator", "architect"],
        "dba_waiver": True,
    },
    "WC-D8": {
        "package": "W-C8",
        "db_layer": True,
        "read_path": ["timer state and process census match the declared owner"],
        "write_path": [
            "exactly one live executor per boundary",
            "alternate runtimes are tombstoned or gated",
            "freeze drill proves direct writes are refused",
        ],
        "rollback": [
            "restore the previous unit schedule and grants",
            "logs and receipts are retained",
        ],
        "integrity": ["7 green soak days; no competing process observed"],
        "evidence_slot": "WC8-OPS-FENCE",
        "signatories": ["operator", "architect", "dba"],
        "dba_waiver": False,
    },
}

# ── Findings (observed, not yet fixed) ────────────────────────────────────
FINDINGS: list[dict[str, Any]] = [
    {
        "id": "WCF-01",
        "severity": "high",
        "package": "W-C2",
        "finding": "At least six code paths can mutate candidate promotion status; four bypass nebula.set_candidate_status (stage3 PATCH, tackle candidate_promote, tackle planner, control-edge cpfPromote).",
        "evidence": "grep census; routes/scripts cited in C-CAN-01..C-CAN-11",
    },
    {
        "id": "WCF-02",
        "severity": "high",
        "package": "W-C2",
        "finding": "stage3 promotes in two steps: POST spawn-requirement, then PATCH status. A crash between them leaves a requirement without a promoted candidate (or vice versa).",
        "evidence": "python/promotion-flow/stage3_execute.py::promote",
    },
    {
        "id": "WCF-03",
        "severity": "high",
        "package": "W-C3",
        "finding": "Every service connects as pguser, which holds INSERT/UPDATE/DELETE/TRUNCATE on nebula.harvest_candidates_history and semantics.canonical_asset. 'Zero direct writers' cannot be certified until distinct app roles exist.",
        "evidence": "information_schema.role_table_grants; V146's C1 finding repeats here",
    },
    {
        "id": "WCF-04",
        "severity": "high",
        "package": "W-C3",
        "finding": "Identity coverage is incomplete: 7,588 of 11,672 candidates have no asset_id; 3,372 active candidate assets are orphans; 3,370 candidate assets carry no canonical_key.",
        "evidence": "live census in SNAPSHOT",
    },
    {
        "id": "WCF-05",
        "severity": "high",
        "package": "W-C4",
        "finding": "resolution.canonical_asset holds 13 rows with canonical_key = NULL (the W-A3 unidentified set); the mirror cannot be promoted as authoritative until W-A3 dispositions land.",
        "evidence": "collect_canonical_assets; W-A §2 W-A3",
    },
    {
        "id": "WCF-06",
        "severity": "high",
        "package": "W-C3",
        "finding": "Voyager mint paths UPDATE canonical_key in place on conflict, while candidate backfill uses DO NOTHING. Two incompatible identity policies on the same table.",
        "evidence": "python/voyager-adapter/db.py; python/voyager/src/voyager_semantics_adapter.py; bin/candidate-asset-backfill.py",
    },
    {
        "id": "WCF-07",
        "severity": "medium",
        "package": "W-C7",
        "finding": "All 21 promoted-candidate requirements link to conduit plan 0005 because stage3 hard-codes planRef='0005'; there is no per-candidate plan/WR lineage.",
        "evidence": "requirements.conduit_plan_id group-by; stage3_execute.py::promote",
    },
    {
        "id": "WCF-08",
        "severity": "medium",
        "package": "W-C8",
        "finding": "The shortlist timer is disabled while stage1 writers remain in the tree; a routine 'start all timers' action would re-introduce a second manifest emitter.",
        "evidence": "systemctl --user is-enabled promotion-shortlist.timer = disabled",
    },
    {
        "id": "WCF-09",
        "severity": "medium",
        "package": "W-C8",
        "finding": "Alternate runtimes (moleculer/nebula, adonisjs control-edge) and the nexus-janitor clone carry full competing write surfaces but are not listening; they are deployable bypasses.",
        "evidence": "process/port census; duplicate route implementations",
    },
    {
        "id": "WCF-10",
        "severity": "medium",
        "package": "W-C2",
        "finding": "Candidate status vocabulary has drifted: 11,624 NULL, 21 promoted, plus active/approved/rejected/superseded/struck values outside the transition function's vocabulary.",
        "evidence": "nebula.harvest_candidates status group-by",
    },
    {
        "id": "WCF-11",
        "severity": "medium",
        "package": "W-C2",
        "finding": "nebula.set_candidate_status exists in the live DB and the CI bootstrap dump, but has no versioned V-migration; the canonical transition function can drift from the repo.",
        "evidence": "grep finds only sql/ci-bootstrap/nexus-ci-bootstrap.sql",
    },
    {
        "id": "WCF-12",
        "severity": "low",
        "package": "W-C7",
        "finding": "26 candidate->plan cross-reference edges exist for 21 promoted candidates; five are legacy/extra and have no explicit disposition.",
        "evidence": "nebula.cross_references rel_type='ag:spawns_plan' group-by",
    },
    {
        "id": "WCF-13",
        "severity": "low",
        "package": "W-C7",
        "finding": "harvest_candidate_embeddings are keyed by harvest_id + candidate_index, not candidate_id, so embeddings cannot be joined to promotion identity without a title/index recipe.",
        "evidence": "column census; 753 rows",
    },
]

WAVE: dict[str, Any] = {
    "plan": "8261665",
    "rerun_of": "8261655",
    "wave": "W-C",
    "title": "Consumer migration: writer fencing + Stage D gates",
    "docket": "c7c26160-5b22-463f-9ee0-6e4784bae0e8",
    "dependencies": {
        "W-A": "8261653 — completed / REVIEW_PASS; merged PR #780",
        "W-B": "8261654 — completed / REVIEW_PASS; merged PR #799",
    },
    "scope_class": "M-sized packages; wave total L",
    "stop_condition": {
        "wave_level": (
            "All consumers of promotion identity are migrated to the canonical write paths, "
            "read/write parity is verified for every package, and zero data loss is proven "
            "by monotonic row counts, preserved ids and an append-only audit trail."
        ),
        "non_authoritative": (
            "Each package stops at 'gate satisfied and evidence recorded'. That is not a "
            "claim that the runtime is universally correct; it is the named owner's "
            "attested stopping point for that boundary."
        ),
        "zero_data_loss": [
            "source row counts never decrease (expiry/archive rows are retained)",
            "every claimed candidate_id, candidate_set_key and canonical_asset_id is preserved",
            "the 21 promoted candidates remain resolvable to exactly one requirement",
            "all 190 manifests remain readable with additive-only changes",
            "no DELETE or TRUNCATE is executed by a W-C package",
        ],
        "rollback": (
            "Each gate names its own restore step. Rollback never deletes canonical history; "
            "it re-grants the previous writer and restores the prior code path for the drain window."
        ),
    },
    "open_findings": len(FINDINGS),
}


def inventory() -> dict[str, Any]:
    """Return the full deterministic W-C registry."""
    return {
        "wave": WAVE,
        "snapshot": SNAPSHOT,
        "identity_model": IDENTITY_MODEL,
        "fences": FENCES,
        "packages": PACKAGES,
        "consumers": CONSUMERS,
        "gates": GATES,
        "findings": FINDINGS,
    }


def dumps(doc: dict[str, Any] | None = None) -> str:
    """Stable serialization used by both ``--write`` and the drift test."""
    doc = inventory() if doc is None else doc
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


# ── Structural validation ────────────────────────────────────────────────
_ALLOWED_MODES = {"writer", "reader", "writer+reader", "scheduler", "data"}
_ALLOWED_RUNTIMES = {"live", "dormant", "offline", "manual", "future"}
_EXTERNAL_DEPS = {"W-A1", "W-A2", "W-A3", "W-A4", "W-A5", "W-A6", "W-A7",
                  "W-B5", "W-B6", "V198", "V205", "DBA roles",
                  "architect lineage ruling"}


def _dependency_cycles(packages: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Return dependency cycles among W-C packages (external deps ignored)."""
    graph: dict[str, list[str]] = {
        pid: [d for d in pkg.get("dependencies", []) if d in packages]
        for pid, pkg in packages.items()
    }
    cycles: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt, 0) == 0:
                visit(nxt)
            elif state.get(nxt) == 1:
                start = stack.index(nxt)
                cycles.append(stack[start:] + [nxt])
        stack.pop()
        state[node] = 2

    for node in graph:
        if state.get(node, 0) == 0:
            visit(node)
    return cycles


def validate(doc: dict[str, Any] | None = None) -> list[str]:
    """Return a list of structural errors (empty means valid)."""
    doc = inventory() if doc is None else doc
    errors: list[str] = []
    packages = doc.get("packages", {})
    consumers = doc.get("consumers", [])
    gates = doc.get("gates", {})
    fences = doc.get("fences", {})
    findings = doc.get("findings", [])

    if not packages:
        errors.append("no packages")
    if not consumers:
        errors.append("no consumers")
    if not gates:
        errors.append("no gates")

    seen_ids: set[str] = set()
    by_package: dict[str, int] = {pid: 0 for pid in packages}
    for consumer in consumers:
        cid = consumer.get("id")
        if not cid:
            errors.append("consumer without id")
            continue
        if cid in seen_ids:
            errors.append(f"duplicate consumer id: {cid}")
        seen_ids.add(cid)
        pkg = consumer.get("package")
        if pkg not in packages:
            errors.append(f"{cid}: unknown package {pkg!r}")
        else:
            by_package[pkg] += 1
        if consumer.get("mode") not in _ALLOWED_MODES:
            errors.append(f"{cid}: bad mode {consumer.get('mode')!r}")
        if consumer.get("runtime") not in _ALLOWED_RUNTIMES:
            errors.append(f"{cid}: bad runtime {consumer.get('runtime')!r}")
        for extra in consumer.get("also_touches", []):
            if extra not in packages:
                errors.append(f"{cid}: unknown also_touches package {extra!r}")

    for pid, pkg in packages.items():
        for field in ("title", "owner", "stop_condition", "state"):
            if not pkg.get(field):
                errors.append(f"{pid}: missing {field}")
        if not isinstance(pkg.get("dependencies"), list):
            errors.append(f"{pid}: dependencies must be a list")
        if not pkg.get("evidence_outputs"):
            errors.append(f"{pid}: missing evidence_outputs")
        if pkg.get("fence") not in fences:
            errors.append(f"{pid}: unknown fence {pkg.get('fence')!r}")
        gate_id = pkg.get("gate")
        if gate_id not in gates:
            errors.append(f"{pid}: unknown gate {gate_id!r}")
        elif gates[gate_id].get("package") != pid:
            errors.append(f"{pid}: gate {gate_id} belongs to {gates[gate_id].get('package')!r}")
        if by_package.get(pid, 0) == 0 and not pkg.get("inventory_only"):
            errors.append(f"{pid}: no consumers")
        if pkg.get("inventory_only") and pkg.get("fence") != "F0":
            errors.append(f"{pid}: inventory-only packages must use F0")
        for dep in pkg.get("dependencies", []):
            if dep not in packages and dep not in _EXTERNAL_DEPS:
                errors.append(f"{pid}: unknown dependency {dep!r}")

    for gate_id, gate in gates.items():
        for family in ("read_path", "write_path", "rollback"):
            if not gate.get(family):
                errors.append(f"{gate_id}: missing {family}")
        if gate.get("package") not in packages:
            errors.append(f"{gate_id}: unknown package {gate.get('package')!r}")
        signatories = gate.get("signatories") or []
        if gate.get("db_layer"):
            if set(signatories) != {"operator", "architect", "dba"}:
                errors.append(f"{gate_id}: DB-layer gate needs operator+architect+dba")
        else:
            if set(signatories) != {"operator", "architect"} or not gate.get("dba_waiver"):
                errors.append(f"{gate_id}: non-DB gate needs operator+architect + dba_waiver")
        if not gate.get("evidence_slot"):
            errors.append(f"{gate_id}: missing evidence_slot")

    seen_findings: set[str] = set()
    for finding in findings:
        fid = finding.get("id")
        if fid in seen_findings:
            errors.append(f"duplicate finding id: {fid}")
        seen_findings.add(fid)
        if finding.get("package") not in packages:
            errors.append(f"{fid}: unknown package {finding.get('package')!r}")
        if not finding.get("evidence"):
            errors.append(f"{fid}: missing evidence")

    for cycle in _dependency_cycles(packages):
        errors.append("dependency cycle: " + " -> ".join(cycle))

    # The wave must declare its W-A/W-B gate.
    deps = doc.get("wave", {}).get("dependencies", {})
    if "W-A" not in deps or "W-B" not in deps:
        errors.append("wave must declare W-A and W-B dependencies")

    return errors


# ── Live census (read-only) ───────────────────────────────────────────────
_CENSUS_SQL = """
SELECT 'semantics_canonical_asset_total', count(*) FROM semantics.canonical_asset
UNION ALL SELECT 'semantics_canonical_asset_active', count(*) FROM semantics.canonical_asset WHERE expired_at IS NULL
UNION ALL SELECT 'candidate_assets_active', count(*) FROM semantics.canonical_asset
          WHERE canonical_asset_id LIKE 'asset:nexus:nebula_harvest_candidates:%' AND expired_at IS NULL
UNION ALL SELECT 'candidate_assets_with_canonical_key', count(*) FROM semantics.canonical_asset
          WHERE canonical_asset_id LIKE 'asset:nexus:nebula_harvest_candidates:%'
            AND expired_at IS NULL AND canonical_key IS NOT NULL
UNION ALL SELECT 'candidate_assets_without_canonical_key', count(*) FROM semantics.canonical_asset
          WHERE canonical_asset_id LIKE 'asset:nexus:nebula_harvest_candidates:%'
            AND expired_at IS NULL AND canonical_key IS NULL
UNION ALL SELECT 'candidate_assets_orphan', count(*) FROM semantics.canonical_asset ca
          WHERE ca.canonical_asset_id LIKE 'asset:nexus:nebula_harvest_candidates:%'
            AND ca.expired_at IS NULL
            AND NOT EXISTS (SELECT 1 FROM nebula.harvest_candidates h
                            WHERE ca.canonical_asset_id =
                                  'asset:nexus:nebula_harvest_candidates:' || h.id)
UNION ALL SELECT 'harvest_candidates', count(*) FROM nebula.harvest_candidates
UNION ALL SELECT 'harvest_candidates_history', count(*) FROM nebula.harvest_candidates_history
UNION ALL SELECT 'harvest_candidates_with_asset_id', count(*) FROM nebula.harvest_candidates WHERE asset_id IS NOT NULL
UNION ALL SELECT 'harvest_candidates_without_asset_id', count(*) FROM nebula.harvest_candidates WHERE asset_id IS NULL
UNION ALL SELECT 'harvest_candidates_promoted', count(*) FROM nebula.harvest_candidates WHERE status = 'promoted'
UNION ALL SELECT 'harvest_candidates_null_status', count(*) FROM nebula.harvest_candidates WHERE status IS NULL
UNION ALL SELECT 'requirements_total', count(*) FROM nebula.requirements
UNION ALL SELECT 'requirements_with_candidate_id', count(*) FROM nebula.requirements WHERE candidate_id IS NOT NULL
UNION ALL SELECT 'requirements_linked_to_plan_0005', count(*) FROM nebula.requirements
          WHERE candidate_id IS NOT NULL AND conduit_plan_id = '0005'
UNION ALL SELECT 'candidate_to_plan_crossrefs', count(*) FROM nebula.cross_references
          WHERE rel_type = 'ag:spawns_plan' AND source_type IN ('harvest_candidate', 'candidate')
UNION ALL SELECT 'promotion_batch', count(*) FROM resolution.promotion_batch
UNION ALL SELECT 'promotion_batch_candidate', count(*) FROM resolution.promotion_batch_candidate
UNION ALL SELECT 'resolution_canonical_asset', count(*) FROM resolution.canonical_asset
UNION ALL SELECT 'resolution_canonical_asset_with_key', count(*) FROM resolution.canonical_asset WHERE canonical_key IS NOT NULL
UNION ALL SELECT 'resolution_receipt', count(*) FROM resolution.receipt
UNION ALL SELECT 'resolution_ticket', count(*) FROM resolution.ticket
UNION ALL SELECT 'candidate_state_records', count(DISTINCT oav.object_id)
          FROM shrapnel.object_attribute_value oav
          JOIN shrapnel.field f ON f.id = oav.field_id
          WHERE f.name = 'asset_id' AND oav.value_id IS NOT NULL
UNION ALL SELECT 'candidate_embeddings', count(*) FROM nebula.harvest_candidate_embeddings
"""


def _manifest_census(manifest_dir: str | Path) -> dict[str, int]:
    """Filesystem census of the promotion manifests (read-only)."""
    path = Path(manifest_dir)
    counts = {
        "manifests": 0,
        "manifest_candidate_entries": 0,
        "manifest_distinct_candidates": 0,
        "manifest_identity_pins": 0,
        "manifest_superseded": 0,
        "manifest_executed": 0,
        "manifest_promoted_flags": 0,
    }
    ids: set[str] = set()
    for file in sorted(path.glob("batch-*.json")):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        counts["manifests"] += 1
        counts["manifest_identity_pins"] += 1 if doc.get("identity_pin") else 0
        counts["manifest_superseded"] += 1 if doc.get("superseded_by") else 0
        counts["manifest_executed"] += 1 if doc.get("executed") else 0
        for candidate in doc.get("candidates", []):
            counts["manifest_candidate_entries"] += 1
            if candidate.get("id"):
                ids.add(str(candidate["id"]))
            counts["manifest_promoted_flags"] += 1 if candidate.get("promoted") else 0
    counts["manifest_distinct_candidates"] = len(ids)
    return counts


def census(
    dsn: str = DEFAULT_DSN,
    manifest_dir: str | Path = DEFAULT_MANIFEST_DIR,
) -> dict[str, Any]:
    """Read-only live census. Requires psycopg2 and a reachable database."""
    import psycopg2  # lazy import: structural validation must not need a DB

    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(_CENSUS_SQL)
            counts = {name: int(value) for name, value in cur.fetchall()}
    finally:
        conn.close()
    counts.update(_manifest_census(manifest_dir))
    return {
        "captured_at": SNAPSHOT["captured_at"],
        "database": dsn.split("@")[-1],
        "manifest_dir": str(manifest_dir),
        "counts": counts,
    }


def _write_artifact(path: str | Path) -> None:
    Path(path).write_text(dumps(), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--validate", action="store_true", help="validate structure only")
    parser.add_argument("--census", action="store_true", help="run the live read-only census")
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--manifest-dir", default=str(DEFAULT_ROOT / DEFAULT_MANIFEST_DIR))
    parser.add_argument("--write", metavar="PATH", help="write the deterministic JSON artifact")
    args = parser.parse_args(argv)

    if args.validate:
        errors = validate()
        if errors:
            for error in errors:
                print(f"invalid: {error}", file=sys.stderr)
            return 1
        print(
            f"ok: {len(PACKAGES)} packages, {len(CONSUMERS)} consumers, "
            f"{len(GATES)} gates, {len(FINDINGS)} findings"
        )
        return 0

    if args.census:
        print(json.dumps(census(args.dsn, args.manifest_dir), indent=2, sort_keys=True))
        return 0

    if args.write:
        _write_artifact(args.write)
        print(f"wrote {args.write}")
        return 0

    print(dumps(), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
