#!/usr/bin/env python3
"""
Unified Semantic Search: Knowledge Graph + Harvest Pipeline + Semantics + Agent Records

Queries all four pgvector embed layers simultaneously:
    - nebula.harvest_candidate_embeddings      (harvested candidates: "what did we harvest?")
    - knowledge.graph_entity_embeddings        (curated KG entities: work_requests, plans, actors...)
    - semantics.source_observation_embeddings  (observed: transcripts, session logs, audit docs)
    - nebula.agent_record_embeddings           (agent records: "was this discussed/done?")

Merges results with provenance labels and returns sorted by relevance.

Usage:
    source /home/codex/dev/nexus/python/rover/.venv/bin/activate
    python3 bin/unified_semantic_search.py "TypeSpec contract-first architecture"
    python3 bin/unified_semantic_search.py "agent orchestration leases" --limit 10
    python3 bin/unified_semantic_search.py "TypeSpec" --json
    # Restrict to the agent layer, only report/engineering_log records, sim >= 0.55:
    python3 bin/unified_semantic_search.py "conduit work requests" \
        --layers agent --record-types report,engineering_log --min-similarity 0.55
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

LOG_DIR = Path("/home/codex/dev/nexus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "unified_semantic_search.log"),
    ],
)
log = logging.getLogger("unified_search")

# ── Config ─────────────────────────────────────────────────────
# Embedding provider is governed by embed_client.py (decision 8ae276bf):
# local ollama on helium (default) -> Gemini -> NVIDIA NIM -> OpenRouter.
# These constants are legacy defaults; the actual embed call uses
# embed_client.embed_one() which reads its own env-driven provider chain.
OLLAMA_URL = os.environ.get('EMBED_OLLAMA_URL', 'http://127.0.0.1:11434')
EMBED_MODEL = os.environ.get('EMBED_MODEL', 'nomic-embed-text')
DOCKER_PSQL = [
    "docker", "exec", "-i", "pgvector_db",
    "psql", "-U", "pguser", "-d", "nexus",
    "-t", "-A", "-q",
]


def generate_query_embedding(query: str) -> list[float] | None:
    """Embedding via tiered provider chain (decision 8ae276bf).
    NIM/Gemini/OpenRouter external default; local ollama offline fallback.
    Returns None on total failure -> callers keep stay-pending semantics."""
    try:
        from embed_client import embed_one  # same dir
        vec, provider = embed_one(query)
        log.info("query embedding via %s", provider)
        return vec
    except Exception as e:
        log.error("E_TRANSIENT_LLM_UNAVAILABLE: %s", e)
        return None


def search_harvest_candidates(query_embedding: list[float], limit: int = 10) -> list[dict]:
    """Search harvest candidate embeddings via cosine similarity."""
    if not query_embedding:
        return []

    vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    sql = f"""
    SELECT id, {_sanitize_text('candidate_title')} AS title,
           {_sanitize_text('intent_text')} AS description,
           (1 - (embedding <=> '{vec_str}'::vector))::real AS similarity,
           {_sanitize_text('source_filename')} AS source,
           'harvested' AS provenance,
           'candidate' AS result_type
    FROM nebula.harvest_candidate_embeddings
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> '{vec_str}'::vector
    LIMIT {limit}
    """

    result = subprocess.run(
        DOCKER_PSQL + ["-F", "\t", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.error("search_harvest_candidates SQL failed: %s", result.stderr.strip()[:300])
        return []

    results = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            try:
                results.append({
                    "id": parts[0],
                    "title": parts[1],
                    "description": parts[2][:300],
                    "similarity": float(parts[3]),
                    "source": parts[4],
                    "provenance": parts[5],
                    "result_type": parts[6],
                })
            except (ValueError, IndexError):
                continue

    return results


def _sanitize_text(expr: str) -> str:
    """Collapse newlines/tabs inside a text column so each psql row is one
    parseable line (the legacy pipe splitter and the new tab splitter both
    depend on it)."""
    return (
        f"replace(replace(replace({expr}, chr(10), ' '), chr(13), ' '), chr(9), ' ')"
    )


def search_knowledge_entities(query_embedding: list[float], limit: int = 10) -> list[dict]:
    """Search knowledge entity embeddings via cosine similarity (pgvector)."""
    if not query_embedding:
        return []

    vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    sql = f"""
    SELECT ee.id, ge.section, ge.entity_type, ge.status,
           {_sanitize_text('ee.name')} AS title,
           {_sanitize_text('COALESCE(ge.description, ee.embed_text)')} AS description,
           (1 - (ee.embedding <=> '{vec_str}'::vector))::real AS similarity,
           'curated' AS provenance,
           'knowledge_entity' AS result_type
    FROM knowledge.graph_entity_embeddings ee
    JOIN knowledge.graph_entities ge ON ge.id = ee.entity_id
    WHERE ee.embedding IS NOT NULL
    ORDER BY ee.embedding <=> '{vec_str}'::vector
    LIMIT {limit}
    """

    result = subprocess.run(
        DOCKER_PSQL + ["-F", "\t", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.error("search_knowledge_entities SQL failed: %s", result.stderr.strip()[:300])
        return []

    results = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 9:
            try:
                results.append({
                    "id": parts[0],
                    "section": parts[1],
                    "entity_type": parts[2],
                    "status": parts[3],
                    "title": parts[4],
                    "description": parts[5][:300] if parts[5] else "",
                    "similarity": float(parts[6]),
                    "provenance": parts[7],
                    "result_type": parts[8],
                })
            except (ValueError, IndexError):
                continue

    return results


def search_source_observations(query_embedding: list[float], limit: int = 10) -> list[dict]:
    """Search source-observation embeddings (transcripts, session logs, audit docs)."""
    if not query_embedding:
        return []

    vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    sql = f"""
    SELECT see.id, see.asset_kind, COALESCE(see.platform, ''), see.raw_location,
           {_sanitize_text('see.embed_text')} AS embed_text,
           (1 - (see.embedding <=> '{vec_str}'::vector))::real AS similarity,
           'observed' AS provenance,
           'source_observation' AS result_type
    FROM semantics.source_observation_embeddings see
    WHERE see.embedding IS NOT NULL
    ORDER BY see.embedding <=> '{vec_str}'::vector
    LIMIT {limit}
    """

    # Tab-delimited output: content-rich embed_text often contains '|'
    # (markdown tables, shell pipes), which would break a pipe split.
    result = subprocess.run(
        DOCKER_PSQL + ["-F", "\t", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.error("search_source_observations SQL failed: %s", result.stderr.strip()[:300])
        return []

    results = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 8:
            try:
                raw_location = parts[3]
                results.append({
                    "id": parts[0],
                    "section": "source_observation",
                    "entity_type": parts[1],
                    "status": parts[2],
                    "title": raw_location.rsplit("/", 1)[-1],
                    "description": parts[4][:300] if parts[4] else "",
                    "similarity": float(parts[5]),
                    "source": raw_location,
                    "provenance": parts[6],
                    "result_type": parts[7],
                })
            except (ValueError, IndexError):
                continue

    return results


# Keep in sync with the `recordTypes` enum in
# nexus/typescript/knowledge-mcp/src/tools/index.ts.
AGENT_RECORD_TYPES = {
    "report", "engineering_log", "architecture_note", "prompt",
    "assessment", "analysis", "response", "inspection", "decision",
}


def search_agent_records(query_embedding: list[float], limit: int = 10,
                         record_types: set[str] | None = None) -> list[dict]:
    """Search agent-record embeddings (\"was this discussed/done before?\").

    record_types: if provided, only these record types are returned
    (e.g. exclude 'inspection' to suppress .gitkeep-style noise).
    """
    if not query_embedding:
        return []

    vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    record_filter = ""
    if record_types:
        unknown = sorted(record_types - AGENT_RECORD_TYPES)
        if unknown:
            log.warning("Ignoring unknown agent record types: %s", unknown)
        known = sorted(record_types & AGENT_RECORD_TYPES)
        if not known:
            # Every requested type was invalid — returning everything would make
            # a typoed filter masquerade as "matched nothing". Return nothing.
            log.warning("No valid record types remain after filtering; returning no results")
            return []
        in_list = ", ".join(f"'{rt}'" for rt in known)
        record_filter = f"AND COALESCE(are.record_type, '') IN ({in_list})"

    sql = f"""
    SELECT are.id, COALESCE(are.role, ''), COALESCE(are.record_type, ''),
           COALESCE(are.title, ''),
           {_sanitize_text('are.embed_text')} AS embed_text,
           (1 - (are.embedding <=> '{vec_str}'::vector))::real AS similarity,
           'agent_record' AS provenance,
           'agent_record' AS result_type
    FROM nebula.agent_record_embeddings are
    WHERE are.embedding IS NOT NULL {record_filter}
    ORDER BY are.embedding <=> '{vec_str}'::vector
    LIMIT {limit}
    """

    result = subprocess.run(
        DOCKER_PSQL + ["-F", "\t", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.error("search_agent_records SQL failed: %s", result.stderr.strip()[:300])
        return []

    results = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 8:
            try:
                results.append({
                    "id": parts[0],
                    "section": "agent_record",
                    "entity_type": parts[2],
                    "status": parts[1],
                    "title": parts[3],
                    "description": parts[4][:300] if parts[4] else "",
                    "similarity": float(parts[5]),
                    "provenance": parts[6],
                    "result_type": parts[7],
                })
            except (ValueError, IndexError):
                continue

    return results


def merge_and_rank(harvest_results, knowledge_results, observation_results,
                   agent_record_results, limit=15, min_similarity: float = 0.0):
    """Merge results from all four sources, filtered by similarity floor,
    sorted by cosine similarity."""
    merged = []
    for lst in (harvest_results, knowledge_results, observation_results, agent_record_results):
        merged.extend(r for r in lst if r["similarity"] >= min_similarity)

    # Sort by similarity descending (all sources use cosine similarity now)
    merged.sort(key=lambda x: -x["similarity"])

    return merged[:limit]


def search(query: str, limit: int = 15, layers: set[str] | None = None,
           record_types: set[str] | None = None,
           min_similarity: float = 0.0) -> dict:
    """Run the unified search and return results. layers defaults to all four.

    record_types restricts the agent layer to the given record types;
    min_similarity drops results below the threshold across all layers.
    """
    if layers is None:
        layers = {"harvest", "kg", "observation", "agent"}
    log.info("Unified search: '%s' (limit=%d, layers=%s, record_types=%s, min_sim=%.2f)",
             query, limit, sorted(layers), sorted(record_types) if record_types else None, min_similarity)

    # Generate embedding for semantic search
    start = time.time()
    query_embedding = generate_query_embedding(query)
    embed_time = time.time() - start

    if query_embedding:
        log.info("Query embedding dimension: %d (%.2fs)", len(query_embedding), embed_time)

    harvest_results = search_harvest_candidates(query_embedding, limit=limit) if "harvest" in layers else []
    knowledge_results = search_knowledge_entities(query_embedding, limit=limit) if "kg" in layers else []
    observation_results = search_source_observations(query_embedding, limit=limit) if "observation" in layers else []
    agent_record_results = search_agent_records(
        query_embedding, limit=limit, record_types=record_types,
    ) if "agent" in layers else []

    log.info("Harvest (cosine): %d results", len(harvest_results))
    log.info("Knowledge (cosine): %d results", len(knowledge_results))
    log.info("Observations (cosine): %d results", len(observation_results))
    log.info("Agent records (cosine): %d results", len(agent_record_results))

    # Merge and rank
    merged = merge_and_rank(
        harvest_results, knowledge_results,
        observation_results, agent_record_results,
        limit=limit, min_similarity=min_similarity,
    )

    return {
        "query": query,
        "min_similarity": min_similarity,
        "total_results": len(merged),
        "harvest_results": len(harvest_results),
        "knowledge_results": len(knowledge_results),
        "observation_results": len(observation_results),
        "agent_record_results": len(agent_record_results),
        "results": merged,
    }


def main():
    parser = argparse.ArgumentParser(description="Unified semantic search across KG entities, harvests, source observations, and agent records")
    parser.add_argument("query", help="Search query string")
    parser.add_argument("--limit", type=int, default=15, help="Max results (default: 15)")
    parser.add_argument("--layers", default="harvest,kg,observation,agent",
                        help="Comma-separated layers to search: harvest,kg,observation,agent (default: all)")
    parser.add_argument("--record-types", default=None,
                        help="Comma-separated agent record types to INCLUDE (report,engineering_log,architecture_note,prompt,assessment,analysis,response,inspection,decision). Applies to the agent layer only.")
    parser.add_argument("--min-similarity", type=float, default=0.0,
                        help="Only return results with similarity >= this value (default: 0.0)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    layers = {s.strip().lower() for s in args.layers.split(",") if s.strip()}
    record_types = {s.strip().lower() for s in args.record_types.split(",") if s.strip()} if args.record_types else None
    result = search(
        args.query, limit=args.limit, layers=layers,
        record_types=record_types, min_similarity=args.min_similarity,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        # Pretty print
        print(f"\nQuery: \"{result['query']}\"")
        if result.get("min_similarity"):
            print(f"Min similarity: {result['min_similarity']:.2f}")
        print(
            f"Results: {result['total_results']} "
            f"({result['harvest_results']} harvested, {result['knowledge_results']} curated, "
            f"{result['observation_results']} observed, {result['agent_record_results']} agent records)"
        )
        print("-" * 80)

        icons = {
            "harvested": "📦",
            "curated": "🧠",
            "observed": "📄",
            "agent_record": "📝",
        }
        for i, r in enumerate(result["results"], 1):
            icon = icons.get(r.get("provenance", ""), "•")
            print(f"\n{i}. {icon} [{r.get('provenance', '?').upper()}] {r['title']}")
            print(f"   Similarity: {r.get('similarity', 0):.4f}")
            if r.get("section"):
                print(f"   Section: {r['section']} ({r.get('entity_type', '')})")
            if r.get("status"):
                print(f"   Status: {r['status']}")
            if r.get("source"):
                print(f"   Source: {r['source']}")
            desc = r.get("description", "")
            if desc:
                print(f"   {desc[:200]}")

        print(f"\n{'-' * 80}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
