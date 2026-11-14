#!/usr/bin/env python3
"""
Batch Embed & Match — Cross-Schema Embedding Stage

Generates vector embeddings for un-embedded harvest candidates using Ollama's
nomic-embed-text model, matches them against knowledge entities via pgvector
cosine similarity, and creates cross_schema cross-references.

Usage:
    source /home/codex/dev/nexus/python/rover/.venv/bin/activate
    python3 bin/batch_embed_and_match.py [--dry-run] [--threshold 0.65]
"""

import json
import logging
import sys
import time
import uuid
import urllib.request
import urllib.error
import subprocess
from pathlib import Path

LOG_DIR = Path("/home/codex/dev/nexus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("batch_embed")

NEBULA_API = "http://localhost:3101/api"
import os
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/embeddings"
EMBED_MODEL = "nomic-embed-text"
DOCKER_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus", "-v", "ON_ERROR_STOP=1"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "batch_embed_and_match.log"),
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


def nebula_get(path: str) -> dict | list:
    url = f"{NEBULA_API}{path}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def nebula_post(path: str, body: dict) -> dict:
    url = f"{NEBULA_API}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else "(no body)"
        log.warning("  API %s: %s", e.code, body_text[:200])
        return {"error": True, "status": e.code, "body": body_text[:200]}


def get_ollama_embedding(text: str) -> list[float] | None:
    """Tiered provider chain (decision 8ae276bf); name kept for callers."""
    try:
        from embed_client import embed_one
        vec, _provider = embed_one(text)
        return vec
    except Exception as e:
        log.error("  E_TRANSIENT_LLM_UNAVAILABLE: %s", e)
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Embed & match candidates")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.65, help="Cosine similarity threshold (default: 0.65)")
    parser.add_argument("--limit", type=int, default=None, help="Max candidates to process")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Batch Embed & Match — Cross-Schema Embedding Stage")
    log.info("Model: %s | Threshold: %.2f", EMBED_MODEL, args.threshold)

    # Step 1: Get all candidates
    log.info("Fetching candidates...")
    candidates_data = nebula_get("/harvest-candidates?limit=1000")
    candidates = candidates_data.get("candidates", [])
    log.info("Total candidates: %d", len(candidates))

    # Step 2: Get existing embedded titles
    rc, out = psql("SELECT candidate_title, id::text, model_used FROM nebula.harvest_candidate_embeddings;")
    embedded = {}
    if out:
        for line in out.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                embedded[parts[0].strip()] = {"id": parts[1].strip(), "model": parts[2].strip()}
    log.info("Existing embeddings: %d", len(embedded))

    # Step 3: Get existing cross_schema refs to avoid duplicates
    existing_xrefs = set()
    rc2, out2 = psql("SELECT source_id, target_id FROM nebula.cross_references WHERE rel_type IN ('kv:cross_schema', 'cross_schema');")
    if out2:
        for line in out2.splitlines():
            parts = line.split("|", 1)
            if len(parts) == 2:
                existing_xrefs.add((parts[0].strip(), parts[1].strip()))
    log.info("Existing cross_schema refs: %d", len(existing_xrefs))

    # Step 4: Get knowledge entities for matching
    rc3, out3 = psql("SELECT kg_entity_id, name, embed_text FROM knowledge.graph_entity_embeddings ORDER BY name;")
    knowledge_entities = []
    if out3:
        for line in out3.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                knowledge_entities.append({"kg_id": parts[0].strip(), "name": parts[1].strip(), "text": parts[2].strip()})
    log.info("Knowledge entities: %d", len(knowledge_entities))

    # Step 5: Find candidates needing embeddings
    to_process = []
    for c in candidates:
        title = c.get("title", "")
        intent = c.get("intentDescription") or ""
        if title not in embedded:
            to_process.append({"id": c["id"], "title": title, "intent": intent,
                               # REST returns camelCase (harvestId); older payloads were snake_case.
                               "harvest_id": c.get("harvest_id") or c.get("harvestId") or None,
                               "source": c.get("harvest_source") or c.get("harvestSource") or ""})

    if args.limit:
        to_process = to_process[:args.limit]

    log.info("Candidates to embed: %d", len(to_process))

    if args.dry_run:
        for c in to_process[:5]:
            log.info("  Would embed: %s", c["title"][:60])
        if len(to_process) > 5:
            log.info("  ... and %d more", len(to_process) - 5)
        return 0

    if not to_process:
        log.info("No candidates need embedding.")
        return 0

    # Step 6: Generate embeddings and insert into DB
    log.info("-" * 60)
    log.info("Generating embeddings...")

    embedded_count = 0
    for c in to_process:
        embed_text = f"{c['title']}. {c['intent']}" if c['intent'] else c['title']
        embedding = get_ollama_embedding(embed_text[:2000])
        if not embedding:
            log.warning("  Failed: %s", c["title"][:40])
            continue

        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        embed_id = str(uuid.uuid4())
        harvest_id_sql = f"'{c['harvest_id']}'" if c.get("harvest_id") else "NULL"

        # candidate_index is UNIQUE per (harvest_id, candidate_index) on the
        # history table behind the view — hardcoding 0 silently collapsed all
        # but one candidate per harvest via ON CONFLICT DO NOTHING. Compute
        # the next free index for this harvest instead.
        sql = f"""
        WITH next_idx AS (
            SELECT COALESCE(MAX(h2.candidate_index) + 1, 0) AS idx
              FROM nebula.harvest_candidate_embeddings_history h2
             WHERE h2.harvest_id = {harvest_id_sql}
        )
        INSERT INTO nebula.harvest_candidate_embeddings
            (id, harvest_id, candidate_index, candidate_title, intent_text,
             embedding, source_filename, model_used, created_at)
        SELECT
            '{embed_id}',
            {harvest_id_sql},
            next_idx.idx,
            '{c['title'].replace("'", "''")}',
            '{c['intent'][:500].replace("'", "''")}',
            '{vec_str}'::vector(768),
            '{c['source'].replace("'", "''")}',
            '{EMBED_MODEL}',
            NOW()
        FROM next_idx
        ON CONFLICT DO NOTHING
        RETURNING id;
        """

        rc_sql, inserted = psql(sql)
        if rc_sql == 0 and inserted:
            embedded[c["title"]] = {"id": embed_id, "model": EMBED_MODEL}
            embedded_count += 1
            if embedded_count % 10 == 0:
                log.info("  Embedded %d/%d...", embedded_count, len(to_process))
        elif rc_sql == 0:
            log.warning("  Insert no-op (conflict) for: %s", c["title"][:40])
        else:
            log.warning("  DB insert failed for: %s", c["title"][:40])

    log.info("  Total embedded: %d", embedded_count)

    if embedded_count == 0:
        log.info("No new embeddings to match against.")
        return 0

    # Step 7: Run pgvector cosine similarity matching
    log.info("-" * 60)
    log.info("Running pgvector similarity matching (threshold: %.2f)...", args.threshold)

    # Get the embeddings we just created for matching
    rc4, matching_out = psql(f"""
    SELECT hce.id::text, hce.candidate_title,
           gee.kg_entity_id, gee.name,
           1 - (hce.embedding <=> gee.embedding) AS similarity,
           hce.source_filename
    FROM nebula.harvest_candidate_embeddings hce
    CROSS JOIN knowledge.graph_entity_embeddings gee
    WHERE hce.model_used = '{EMBED_MODEL}'
      AND 1 - (hce.embedding <=> gee.embedding) >= {args.threshold}
    ORDER BY hce.candidate_title, similarity DESC;
    """)

    xrefs_created = 0
    errors = 0
    matched_count = 0

    if matching_out:
        lines = matching_out.splitlines()
        log.info("  Found %d matching pairs", len(lines))
        
        for line in lines:
            parts = line.split("|", 5)
            if len(parts) < 5:
                continue
            embed_id = parts[0].strip()
            candidate_title = parts[1].strip()
            kg_entity_id = parts[2].strip()
            kg_name = parts[3].strip()
            similarity = parts[4].strip()
            source_file = parts[5].strip() if len(parts) > 5 else ""

            xref_key = (embed_id, kg_entity_id)
            if xref_key in existing_xrefs:
                continue

            body = {
                "sourceType": "harvest_candidate_embedding",
                "sourceId": embed_id,
                "targetType": "knowledge_entity_embedding",
                "targetId": kg_entity_id,
                "relType": "kv:cross_schema",
                "metadata": {
                    "version": "v3",
                    "similarity": float(similarity),
                    "knowledge_name": kg_name,
                    "candidate_title": candidate_title,
                    "matching_method": "cosine_similarity_pgvector",
                    "source_filename": source_file,
                }
            }
            result = nebula_post("/cross-references", body)
            if result.get("error"):
                errors += 1
            else:
                xrefs_created += 1
                existing_xrefs.add(xref_key)
                matched_count += 1

    log.info("=" * 60)
    log.info("RESULTS: %d embeddings created, %d cross-schema refs created, %d errors",
             embedded_count, xrefs_created, errors)
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
