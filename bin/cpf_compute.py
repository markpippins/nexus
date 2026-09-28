#!/usr/bin/env python3
"""
cpf_compute.py — Compilation-adjacent Readiness (CPF) Scalar Computation

Computes the compilation_readiness score (0.0–1.0) for each harvest candidate
in the database and writes it to the harvest_candidates table.

SCORING COMPONENTS:
  intent_filled     0.20  — candidate has a non-empty intent_description
  hierarchy_mapped  0.20  — mapped to system (0.10) + subsystem (0.07) + feature (0.03)
  tagged            0.10  — 2+ tags (0.10), 1 tag (0.03), 0 tags (0)
  has_artifacts     0.20  — implementation_notes (0.10) + code_snippets (0.10) non-empty
  deps_resolved     0.20  — all dependency candidates are promoted/completed
  reconciled        0.10  — completed flag is true

Total: 1.00

Usage:
    python3 bin/cpf_compute.py                     # compute all candidates
    python3 bin/cpf_compute.py --candidate <uuid>  # compute single candidate
    python3 bin/cpf_compute.py --dry-run            # preview without writing

Logs go to the repository's logs/ by default; override with CPF_COMPUTE_LOG_DIR.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Log destination is configurable, and defaults to the repository's own
# gitignored logs/ rather than a path baked in at authoring time. The previous
# literal, Path("/home/codex/dev/nexus/logs"), was one machine's checkout, and
# because the mkdir below runs at import time, importing this module on any
# other host died with:
#   PermissionError: [Errno 13] Permission denied: '/home/codex'
# which is how bin/tests/test_cpf_compute_drift_guard.py failed in CI. Set
# CPF_COMPUTE_LOG_DIR to override.
LOG_DIR = Path(
    os.environ.get("CPF_COMPUTE_LOG_DIR")
    or (Path(__file__).resolve().parent.parent / "logs")
)
LOG_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("cpf_compute")

DOCKER_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "cpf_compute.log"),
    ],
)


def psql(sql: str, timeout: int = 30) -> tuple[int, str]:
    try:
        result = subprocess.run(
            DOCKER_PSQL + ["-t", "-A"],
            input=sql, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, "(timeout)"


def is_drift_candidate(candidate: dict) -> bool:
    """True if the candidate is drift-typed (plan 8261640 intake seam).

    Drift candidates own their readiness (severity-based) by design and must be
    excluded from the legacy transcript-derived CPF recompute, which would
    otherwise floor them at ~0.5 and drop them below the 0.7 shortlist threshold.
    """
    return candidate.get("type") == "drift"


def fetch_candidates(candidate_id: str | None = None) -> list[dict]:
    """Fetch candidates with all fields needed for CPF computation."""
    where = ""
    if candidate_id:
        where = f"WHERE hc.id = '{candidate_id}'"

    # Use row_to_json to avoid delimiter issues with text/JSON fields
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                hc.id,
                hc.intent_description,
                hc.system_id,
                hc.subsystem_id,
                hc.feature_id,
                hc.tags,
                hc.implementation_notes,
                hc.code_snippets,
                hc.completed,
                hc.compilation_readiness,
                hc.type
            FROM nebula.harvest_candidates hc
            {where}
            ORDER BY hc.created_at
        ) r;
    """
    rc, out = psql(sql)
    if rc != 0 or not out:
        return []

    candidates = []
    for line in out.splitlines():
        if not line:
            continue
        try:
            c = json.loads(line)
            c["tags"] = c.get("tags") or []
            c["implementation_notes"] = c.get("implementation_notes") or []
            c["code_snippets"] = c.get("code_snippets") or []
            c["old_readiness"] = c.get("compilation_readiness")
            candidates.append(c)
        except json.JSONDecodeError:
            log.warning("Could not parse candidate row: %s", line[:100])
    return candidates


def fetch_all_deps() -> dict[str, list[str]]:
    """Load all dependency edges in one query. Returns {candidate_id: [dep_id, ...]}."""
    sql = "SELECT row_to_json(r)::text FROM (SELECT candidate_id, depends_on_id FROM nebula.candidate_dependencies WHERE valid_until > now()) r;"
    rc, out = psql(sql)
    if rc != 0 or not out:
        return {}
    deps: dict[str, list[str]] = {}
    for line in out.splitlines():
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = d["candidate_id"]
        deps.setdefault(cid, []).append(d["depends_on_id"])
    return deps


def fetch_all_statuses() -> dict[str, tuple[str, bool]]:
    """Load status + completed for ALL candidates in one query. Returns {id: (status, completed)}."""
    sql = """
        SELECT row_to_json(r)::text FROM (
            SELECT id, status, completed FROM nebula.harvest_candidates
        ) r;
    """
    rc, out = psql(sql)
    if rc != 0 or not out:
        return {}
    statuses: dict[str, tuple[str, bool]] = {}
    for line in out.splitlines():
        if not line:
            continue
        try:
            s = json.loads(line)
        except json.JSONDecodeError:
            continue
        statuses[s["id"]] = (s.get("status"), s.get("completed", False))
    return statuses


def resolve_deps(candidate_id: str, dep_ids: list[str],
                 statuses: dict[str, tuple[str, bool]]) -> tuple[float, list[dict]]:
    """Check if all dependencies are resolved, using pre-loaded statuses."""
    if not dep_ids:
        return 1.0, []

    resolved = 0
    total = 0
    details = []
    for dep_id in dep_ids:
        dep_status, dep_completed = statuses.get(dep_id, (None, False))
        total += 1
        is_resolved = (dep_status == "promoted") or dep_completed
        if is_resolved:
            resolved += 1
        details.append({"id": dep_id, "status": dep_status, "resolved": is_resolved})

    proportion = resolved / total if total > 0 else 1.0
    return proportion, details


def compute_readiness(candidate: dict, all_deps: dict[str, list[str]],
                      all_statuses: dict[str, tuple[str, bool]]) -> dict:
    """
    Compute CPF for a single candidate using pre-loaded dependency data.
    Returns dict with score and per-component breakdown.
    """
    components = {}

    # 1. intent_filled (0.20)
    if candidate["intent_description"] and candidate["intent_description"].strip():
        components["intent_filled"] = 0.20
    else:
        components["intent_filled"] = 0.0

    # 2. hierarchy_mapped (0.20)
    hier_score = 0.0
    if candidate["system_id"]:
        hier_score += 0.10
    if candidate["subsystem_id"]:
        hier_score += 0.07
    if candidate["feature_id"]:
        hier_score += 0.03
    components["hierarchy_mapped"] = round(hier_score, 2)

    # 3. tagged (0.10)
    tag_count = len(candidate["tags"])
    if tag_count >= 2:
        components["tagged"] = 0.10
    elif tag_count == 1:
        components["tagged"] = 0.03
    else:
        components["tagged"] = 0.0

    # 4. has_artifacts (0.20)
    art_score = 0.0
    notes = candidate["implementation_notes"]
    if isinstance(notes, list) and len(notes) > 0:
        art_score += 0.10
    snippets = candidate["code_snippets"]
    if isinstance(snippets, list) and len(snippets) > 0:
        art_score += 0.10
    components["has_artifacts"] = art_score

    # 5. reconciled (0.10)
    components["reconciled"] = 0.10 if candidate["completed"] else 0.0

    # 6. deps_resolved (0.20) using pre-loaded data
    dep_ids = all_deps.get(candidate["id"], [])
    prop, dep_detail = resolve_deps(candidate["id"], dep_ids, all_statuses)
    components["deps_resolved"] = round(prop * 0.20, 3)

    total = round(
        components["intent_filled"]
        + components["hierarchy_mapped"]
        + components["tagged"]
        + components["has_artifacts"]
        + components["reconciled"]
        + components["deps_resolved"],
        3,
    )

    return {
        "candidate_id": candidate["id"],
        "score": total,
        "components": {k: v for k, v in components.items() if not k.startswith("_")},
        "dependencies": dep_ids,
        "dep_detail": dep_detail,
        "old_readiness": candidate["old_readiness"],
    }


def batch_update_readiness(updates: list[tuple[str, float]]) -> int:
    """Write multiple CPF scores in a single batch."""
    if not updates:
        return 0
    values = ", ".join(
        f"('{cid}', {score})" for cid, score in updates
    )
    sql = f"""
        UPDATE nebula.harvest_candidates hc
        SET compilation_readiness = v.score,
            updated_at = now()
        FROM (VALUES {values}) AS v(id, score)
        WHERE hc.id = v.id::uuid;
    """
    rc, out = psql(sql, timeout=60)
    if rc == 0:
        # psql returns number of rows affected
        return len(updates)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Compute CPF readiness scores")
    parser.add_argument("--candidate", type=str, default=None,
                        help="Compute for a specific candidate UUID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview scores without writing to DB")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("CPF Readiness Computation")
    log.info("Time: %s", datetime.now().isoformat())
    log.info("Mode: %s", "DRY RUN (no writes)" if args.dry_run else "LIVE (will write)")
    log.info("=" * 60)

    candidates = fetch_candidates(args.candidate)
    log.info("Candidates loaded: %d", len(candidates))

    if not candidates:
        log.info("Nothing to compute.")
        return 0

    # Pre-load dependency graph and statuses (2 queries total, not 2 per candidate)
    log.info("Loading dependency graph...")
    all_deps = fetch_all_deps()
    log.info("  %d edges across %d candidates",
             sum(len(v) for v in all_deps.values()), len(all_deps))
    log.info("Loading candidate statuses...")
    all_statuses = fetch_all_statuses()
    log.info("  %d statuses loaded", len(all_statuses))

    results = []
    updated = 0
    skipped_drift = 0
    for c in candidates:
        # Drift-typed candidates (plan 8261640 intake seam) own their readiness
        # (severity-based: high 0.85 / medium 0.75 / low 0.70) by design — ranked
        # above transcript-derived at equal readiness. The legacy recompute must
        # NOT overwrite that (it has no hierarchy/artifact signal and would floor
        # them at ~0.5, dropping them below the 0.7 shortlist threshold).
        if is_drift_candidate(c):
            skipped_drift += 1
            continue
        r = compute_readiness(c, all_deps, all_statuses)
        results.append(r)
        delta = ""
        if r["old_readiness"] is not None:
            diff = r["score"] - r["old_readiness"]
            delta = f" (Δ{'+' if diff > 0 else ''}{diff:.3f})"
        log.info(
            "  %s → CPF=%.3f%s  [intent=%.2f hier=%.2f tags=%.2f art=%.2f rec=%.2f deps=%.2f]%s",
            c["id"][:8],
            r["score"],
            delta,
            r["components"]["intent_filled"],
            r["components"]["hierarchy_mapped"],
            r["components"]["tagged"],
            r["components"]["has_artifacts"],
            r["components"]["reconciled"],
            r["components"]["deps_resolved"],
            "  ⚡READY" if r["score"] >= 0.7 else "",
        )

    if not args.dry_run:
        batch = [(r["candidate_id"], r["score"]) for r in results]
        updated = batch_update_readiness(batch)
        log.info("DB writes: %d / %d (batched)", updated, len(batch))

    # Summary
    log.info("─" * 60)
    if skipped_drift:
        log.info("Skipped %d drift-typed candidates (readiness owned by drift-intake seam, plan 8261640)", skipped_drift)
    ready = [r for r in results if r["score"] >= 0.7]
    log.info("CPF >= 0.7 (ready): %d / %d", len(ready), len(results))
    for r in ready:
        log.info("  • %s (%.3f) — %d deps",
                 r["candidate_id"][:8], r["score"], len(r["dependencies"]))

    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
