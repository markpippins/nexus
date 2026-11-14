#!/usr/bin/env python3
"""
Reconcile Agent Records — Semantic Similarity for Work Outside WorkRequest Flow

Fetches finalized agent records (engineer engineering_logs, builder
engineering_logs, architect decisions/reports) and uncompleted harvest
candidates from the database, then uses a local Ollama embedding model to
compute dense semantic similarity between candidate intent and agent record
content.

This is the *third* reconciliation path:
  1. Keyword matching against completed plans (reconcile_completed.py)
  2. Embedding matching against completed plans (reconcile_embeddings.py)
  3. Embedding matching against agent records (this script)

Candidates matched to an agent record indicating completed work are marked
`completed = true`.

Embeddings are cached to disk so re-runs are fast.

Usage:
    source /home/codex/dev/nexus/python/rover/.venv/bin/activate
    python3 bin/reconcile_agent_records.py --dry-run
    python3 bin/reconcile_agent_records.py --apply --threshold 0.65
    python3 bin/reconcile_agent_records.py --dry-run --limit 20
    python3 bin/reconcile_agent_records.py --dry-run --top-k 3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from embed_util import (
    CONFIDENCE_ORDER,
    CACHE_DIR,
    DEFAULT_MODEL,
    DOCKER_PSQL,
    OLLAMA_API,
    build_candidate_text,
    cosine_similarity_matrix,
    embed_texts,
    psql,
)

log = logging.getLogger("reconcile_agent_records")

LOG_DIR = Path("/home/codex/dev/nexus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "reconcile_agent_records.log"),
    ],
)


# ── Data fetching ──────────────────────────────────────────────────────

def fetch_finalized_agent_records() -> list[dict]:
    """Fetch agent records that indicate finalized work or decisions.

    Included:
      - engineer + engineering_log
      - builder  + engineering_log
      - architect + decision
      - architect + report

    Excluded:
      - planner, analyst, inspector, reviewer records
      - prompts, responses (conversational, not binding)
      - records whose content contains "not implemented", "will be implemented",
        or "draft" (future/intent language, not completion)
    """
    _, data = psql("""
        SELECT json_agg(json_build_object(
            'id', ar.id,
            'role', ar.role,
            'record_type', ar.record_type,
            'title', ar.title,
            'content', LEFT(ar.content, 1200),
            'tags', ar.tags
        ) ORDER BY ar.created_at DESC)
        FROM nebula.agent_records ar
        WHERE ar.role IN ('engineer', 'builder', 'architect')
          AND ar.record_type IN ('engineering_log', 'decision', 'report')
          AND ar.content NOT ILIKE '%not implemented%'
          AND ar.content NOT ILIKE '%will be implemented%'
          AND ar.content NOT ILIKE '%draft%'
          AND (ar.plan_ref IS NULL OR ar.plan_ref = '')
    """)
    if not data or data == "NULL":
        return []
    return json.loads(data)


def fetch_uncompleted_candidates() -> list[dict]:
    """Fetch all candidates with completed=false."""
    _, data = psql("""
        SELECT json_agg(json_build_object(
            'id', hc.id,
            'title', hc.title,
            'intent', LEFT(COALESCE(hc.intent_description, ''), 800)
        ) ORDER BY hc.created_at DESC)
        FROM nebula.harvest_candidates hc
        WHERE hc.completed = false OR hc.completed IS NULL
    """)
    if not data or data == "NULL":
        return []
    return json.loads(data)


# ── Text preparation ───────────────────────────────────────────────────

def build_agent_record_text(record: dict) -> str:
    """Build a rich text representation of an agent record for embedding."""
    parts = [
        f"Role: {record.get('role', '')}",
        f"Record Type: {record.get('record_type', '')}",
        f"Title: {record.get('title', '')}",
    ]
    tags = record.get("tags")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    content = record.get("content", "")
    if content:
        parts.append(f"Content: {content}")
    return "\n".join(parts)


# ── Matching engine ────────────────────────────────────────────────────

def match_candidates(
    candidates: list[dict],
    candidate_embeddings: np.ndarray,
    records: list[dict],
    record_embeddings: np.ndarray,
    threshold: float,
    top_k: int = 1,
) -> tuple[list[dict], list[dict]]:
    """Match candidates to agent records using cosine similarity of embeddings.
    Returns (matched, unmatched)."""
    sim_matrix = cosine_similarity_matrix(candidate_embeddings, record_embeddings)
    matched = []
    unmatched = []

    for i, candidate in enumerate(candidates):
        sim_row = sim_matrix[i]
        best_indices = np.argsort(sim_row)[::-1][:top_k]
        best_score = float(sim_row[best_indices[0]])

        if best_score >= threshold:
            matched_record_ids = [str(records[idx]["id"]) for idx in best_indices if sim_row[idx] >= threshold]

            if best_score >= 0.80:
                confidence = "high"
            elif best_score >= 0.65:
                confidence = "medium"
            else:
                confidence = "low"

            reasoning_parts = [f"cosine_sim={best_score:.3f}"]
            if len(matched_record_ids) > 1:
                reasoning_parts.append(f"multi_match={len(matched_record_ids)}")

            matched.append({
                "candidate_id": candidate["id"],
                "candidate_title": candidate.get("title", ""),
                "matched_record_ids": matched_record_ids,
                "confidence": confidence,
                "reasoning": ", ".join(reasoning_parts),
                "similarity": best_score,
            })
        else:
            best_record_id = str(records[best_indices[0]]["id"]) if len(records) > 0 else "none"
            unmatched.append({
                "candidate_id": candidate["id"],
                "candidate_title": candidate.get("title", ""),
                "reason": f"Best similarity {best_score:.3f} to agent record {best_record_id} below threshold {threshold}",
                "best_similarity": best_score,
                "best_record_id": best_record_id,
            })

    return matched, unmatched


# ── Main ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Match harvest candidates to finalized agent records using embedding-based semantic similarity"
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't update candidates")
    parser.add_argument("--apply", action="store_true", help="Mark matched candidates as completed")
    parser.add_argument(
        "--threshold", type=float, default=0.48,
        help="Minimum cosine similarity threshold (default: 0.48)"
    )
    parser.add_argument(
        "--top-k", type=int, default=1,
        help="Return top-K record matches per candidate (default: 1)"
    )
    parser.add_argument(
        "--min-confidence", type=str, default=None,
        choices=["low", "medium", "high"],
        help="Only apply matches at or above this confidence level"
    )
    parser.add_argument(
        "--only-high", action="store_true",
        help="Shorthand for --min-confidence high"
    )
    parser.add_argument(
        "--model", type=str, default=DEFAULT_MODEL,
        help=f"Ollama embedding model to use (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Clear the embedding cache before running"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only process the first N candidates (useful for quick tests)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Write match results to JSON file for review"
    )
    args = parser.parse_args()

    if args.top_k < 1:
        parser.error("--top-k must be >= 1")

    if args.only_high:
        args.min_confidence = "high"

    if args.min_confidence and not args.apply and not args.dry_run:
        log.warning("--min-confidence / --only-high without --apply has no effect")

    if args.clear_cache and CACHE_DIR.is_dir():
        log.info("Clearing embedding cache at %s", CACHE_DIR)
        for f in CACHE_DIR.glob("*.npy"):
            f.unlink()

        # Pre-flight Ollama health check
    import os
    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as r:
            models = json.loads(r.read().decode()).get("models", [])
            model_names = {m["name"] for m in models}
            if args.model not in model_names and f"{args.model}:latest" not in model_names:
                log.warning(
                    "Model %s not found in Ollama. Available: %s",
                    args.model, ", ".join(sorted(model_names)[:10]),
                )
    except Exception as e:
        log.error("Ollama not reachable at %s — %s", OLLAMA_URL, e)
        return 1

    log.info("Fetching finalized agent records...")
    records = fetch_finalized_agent_records()
    log.info("  %d finalized agent records found", len(records))

    if not records:
        log.info("No finalized agent records found. Nothing to match against.")
        return 0

    log.info("Fetching uncompleted candidates...")
    candidates = fetch_uncompleted_candidates()
    log.info("  %d uncompleted candidates found", len(candidates))

    if not candidates:
        log.info("All candidates already completed. Nothing to do.")
        return 0

    if args.limit is not None and args.limit > 0:
        candidates = candidates[:args.limit]
        log.info("  --limit %d applied; processing %d candidates", args.limit, len(candidates))

    # Build texts
    log.info("Building agent record texts...")
    record_texts = [build_agent_record_text(r) for r in records]

    log.info("Embedding %d agent records via Ollama (%s)...", len(record_texts), args.model)
    record_embeddings = embed_texts(record_texts, model=args.model)
    log.info("  Agent record embeddings shape: %s", record_embeddings.shape)

    log.info("Building candidate texts...")
    candidate_texts = [build_candidate_text(c) for c in candidates]

    # Embed candidates in batches
    batch_size = 128
    candidate_embeddings_list = []
    log.info("Embedding %d candidates in batches of %d...", len(candidate_texts), batch_size)
    for start in range(0, len(candidate_texts), batch_size):
        end = min(start + batch_size, len(candidate_texts))
        batch = candidate_texts[start:end]
        log.info("  Batch %d/%d (%d texts)...",
                 start // batch_size + 1,
                 (len(candidate_texts) + batch_size - 1) // batch_size,
                 len(batch))
        emb = embed_texts(batch, model=args.model)
        candidate_embeddings_list.append(emb)
    candidate_embeddings = np.concatenate(candidate_embeddings_list, axis=0)
    log.info("  Candidate embeddings shape: %s", candidate_embeddings.shape)

    # Match
    log.info("Computing similarity matrix and matching...")
    matched, unmatched = match_candidates(
        candidates, candidate_embeddings, records, record_embeddings,
        threshold=args.threshold, top_k=args.top_k,
    )

    # Summary
    matched_count = len(matched)
    unmatched_count = len(unmatched)
    log.info("=" * 60)
    log.info(
        "RESULTS: %d matched, %d unmatched across %d candidates",
        matched_count, unmatched_count, matched_count + unmatched_count,
    )
    if matched_count > 0:
        high = sum(1 for m in matched if m.get("confidence") == "high")
        med = sum(1 for m in matched if m.get("confidence") == "medium")
        low = sum(1 for m in matched if m.get("confidence") == "low")
        log.info("  Confidence breakdown: high=%d, medium=%d, low=%d", high, med, low)

        log.info("")
        log.info("Top matches by similarity:")
        for m in sorted(matched, key=lambda x: x.get("similarity", 0), reverse=True)[:20]:
            log.info(
                "  [%s] sim=%.3f %s -> records %s (%s)",
                m["confidence"], m.get("similarity", 0),
                m["candidate_title"][:55],
                ",".join(m["matched_record_ids"]),
                m["reasoning"][:55],
            )
    log.info("=" * 60)

    # Write results to file if requested
    if args.output:
        output = {"matched": matched, "unmatched": unmatched}
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        log.info("Results written to %s", args.output)

    # Filter by min-confidence for --apply
    to_apply = matched
    if args.min_confidence:
        min_level = CONFIDENCE_ORDER.get(args.min_confidence, 0)
        to_apply = [
            m for m in matched
            if CONFIDENCE_ORDER.get(m.get("confidence", "low"), 0) >= min_level
        ]
        log.info(
            "Filtering to --min-confidence=%s: %d of %d matches eligible",
            args.min_confidence, len(to_apply), len(matched),
        )

    # Apply updates
    if args.apply and not args.dry_run:
        log.info("Applying %d matches via direct SQL UPDATE...", len(to_apply))
        ids = [m["candidate_id"] for m in to_apply]
        if ids:
            # IDs are trusted UUIDs from the same database; safe for string interpolation
            id_list = ",".join(f"'{i}'" for i in ids)
            sql = f"UPDATE nebula.harvest_candidates SET completed = true WHERE id IN ({id_list});"
            rc, stdout = psql(sql, timeout=60)
            if rc == 0:
                # stdout format: "UPDATE N"
                parts = stdout.split()
                count = parts[1] if len(parts) > 1 else "?"
                log.info("Marked %s candidates as completed", count)
            else:
                log.error("SQL UPDATE failed: %s", stdout)
    elif args.dry_run:
        log.info("[DRY-RUN] Would mark %d candidates as completed", len(to_apply))
    elif not args.apply:
        log.info("Use --apply to mark candidates as completed, or --output to save results")

    return 0


if __name__ == "__main__":
    sys.exit(main())
