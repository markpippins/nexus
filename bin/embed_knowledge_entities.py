#!/usr/bin/env python3
"""
Knowledge Graph Entity Embedding

Embeds all knowledge.graph_entities into knowledge.graph_entity_embeddings
using Ollama nomic-embed-text (768-dim). Uses psycopg2 for direct PostgreSQL connection.

Usage:
    source /home/codex/dev/nexus/python/rover/.venv/bin/activate
    python3 bin/embed_knowledge_entities.py
    python3 bin/embed_knowledge_entities.py --dry-run
"""

import argparse
import logging
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
        logging.FileHandler(LOG_DIR / "embed_knowledge_entities.log"),
    ],
)
log = logging.getLogger("embed_knowledge")

# ── Config ─────────────────────────────────────────────────────
import os
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")  # legacy local leg (tiered chain in embed_client)
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768

DOCKER_PSQL = [
    "docker", "exec", "-i", "pgvector_db",
    "psql", "-U", "pguser", "-d", "nexus",
    "-t", "-A", "-q",
]


def fetch_knowledge_entities():
    """Fetch knowledge entities not yet embedded, building enriched embed_text."""
    sql = """
    SELECT ge.id, ge.section, ge.entity_id, ge.name,
           ge.entity_type,
           COALESCE(ge.description, '') AS description,
           ge.properties::text AS props_json
    FROM knowledge.graph_entities ge
    LEFT JOIN knowledge.graph_entity_embeddings ee ON ee.entity_id = ge.id
    WHERE ee.id IS NULL
    ORDER BY ge.section, ge.name
    """
    result = subprocess.run(
        DOCKER_PSQL + ["-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.error("Query failed: %s", result.stderr[:200])
        return []

    import json as _json

    entities = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue

        entity_id = parts[0]
        section = parts[1]
        kg_id = parts[2]
        name = parts[3]
        entity_type = parts[4]
        description = parts[5]
        props_raw = parts[6] if len(parts) > 6 else "{}"

        # Build enriched embed_text
        embed_text = description.strip()

        if not embed_text or len(embed_text) < 30:
            # Try to extract text from properties JSONB
            try:
                props = _json.loads(props_raw)
                # Architectural observations: observation + rationale
                for key in ("observation", "rationale", "statement", "description"):
                    val = props.get(key, "")
                    if val and isinstance(val, str) and len(val) > 20:
                        embed_text = val
                        break
                # Rules: may have nested description or statement
                if not embed_text or len(embed_text) < 30:
                    for key in ("description", "statement"):
                        val = props.get(key, "")
                        if val and isinstance(val, str) and len(val) > 10:
                            embed_text = val
                            break
                # Pared KG (plans / work_requests): goal / problem_statement /
                # desired_outcome — combine with the title for richer text.
                if not embed_text or len(embed_text) < 30:
                    raw_title = props.get("title")
                    title = raw_title.strip() if isinstance(raw_title, str) else ""
                    bits = []
                    if title:
                        bits.append(f"Title: {title}")
                    for key in ("goal", "problem_statement", "desired_outcome"):
                        val = props.get(key)
                        if isinstance(val, str) and len(val.strip()) > 5:
                            bits.append(val.strip())
                    if bits:
                        embed_text = " ".join(bits)
            except (_json.JSONDecodeError, TypeError):
                pass

        # Final fallback: use name + context fields
        if not embed_text or len(embed_text) < 10:
            parts_list = [name, section.replace("_", " "), entity_type]
            embed_text = " ".join(p for p in parts_list if p and p.strip())

        entities.append({
            "id": entity_id,
            "section": section,
            "entity_id": kg_id,
            "name": name,
            "embed_text": embed_text,
        })

    log.info("Fetched %d knowledge entities without embeddings", len(entities))
    return entities


def generate_embeddings(entities):
    """Generate embeddings via Ollama nomic-embed-text one entity at a time."""
    embeddings = []
    total = len(entities)
    failures = 0

    client = httpx.Client(timeout=120.0)

    for idx, e in enumerate(entities):
        if idx % 25 == 0:
            log.info("Embedding entity %d/%d...", idx + 1, total)

        if not e["embed_text"].strip():
            failures += 1
            continue

        try:
            from embed_client import embed_one  # tiered chain (8ae276bf)
            emb, _prov = embed_one(e["embed_text"])

            if emb and len(emb) == EMBED_DIM:
                embeddings.append({**e, "embedding": emb})
            else:
                failures += 1
        except Exception as ex:
            failures += 1
            if failures <= 3:
                log.error("Embedding entity %d failed: %s", idx + 1, ex)

        if idx % 10 == 9:
            time.sleep(0.3)

    client.close()
    log.info("Generated %d embeddings (%d failures)", len(embeddings), failures)
    return embeddings


def insert_embeddings(embeddings, dry_run: bool = False):
    """Insert embeddings into knowledge.graph_entity_embeddings via psycopg2."""
    if dry_run:
        log.info("DRY RUN — would insert %d embeddings", len(embeddings))
        for e in embeddings[:3]:
            log.info("  [%s] %s (dim=%d)", e["section"], e["name"][:60], len(e["embedding"]))
        return len(embeddings)

    import psycopg2

    try:
        conn = psycopg2.connect(
            host="localhost", port=5432,
            user="pguser", password="pgpass",
            dbname="nexus"
        )
        conn.autocommit = False
    except Exception as ex:
        log.error("Failed to connect to PostgreSQL: %s", ex)
        return 0

    inserted = 0
    errors = 0
    total = len(embeddings)

    try:
        cur = conn.cursor()

        for idx, e in enumerate(embeddings):
            vec_str = "[" + ",".join(str(x) for x in e["embedding"]) + "]"
            try:
                cur.execute(
                    "INSERT INTO knowledge.graph_entity_embeddings "
                    "(entity_id, section, kg_entity_id, name, embed_text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s::vector) "
                    "ON CONFLICT (entity_id) DO UPDATE SET "
                    "embedding = EXCLUDED.embedding, "
                    "embed_text = EXCLUDED.embed_text, "
                    "name = EXCLUDED.name",
                    (e["id"], e["section"], e["entity_id"], e["name"], e["embed_text"], vec_str)
                )
                inserted += 1
            except Exception as ex:
                errors += 1
                if errors <= 3:
                    log.error("Insert failed for %s: %s", e["name"][:60], ex)

            if (inserted + errors) % 50 == 0:
                conn.commit()
                log.info("  Progress: %d/%d (%d errors)", inserted, total, errors)

        conn.commit()
        cur.close()
    except Exception as ex:
        conn.rollback()
        log.error("Transaction failed: %s", ex)
        errors = total - inserted
    finally:
        conn.close()

    log.info("Inserted %d embeddings (%d errors)", inserted, errors)
    return inserted


def rebuild_index():
    """Rebuild the ivfflat index on knowledge.graph_entity_embeddings."""
    log.info("Rebuilding ivfflat index on knowledge.graph_entity_embeddings...")
    result = subprocess.run(
        DOCKER_PSQL + [
            "-c",
            "REINDEX INDEX IF EXISTS knowledge.idx_kg_entity_embeddings_ivfflat;"
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0:
        log.info("Index rebuilt successfully")
    else:
        log.warning("Index rebuild: %s", result.stderr[:200])


def run_semantic_test():
    """Run a quick semantic search against knowledge entity embeddings."""
    log.info("Running semantic search test on knowledge entity embeddings...")

    try:
        from embed_client import embed_one
        query_embedding, _prov = embed_one("TypeSpec contract architecture governance")
    except Exception as e:
        log.warning("Test query embedding failed (E_TRANSIENT_LLM_UNAVAILABLE): %s", e)
        return

    vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    sql = f"""
    SELECT ee.name, ee.section,
           (1 - (ee.embedding <=> '{vec_str}'::vector))::real AS similarity
    FROM knowledge.graph_entity_embeddings ee
    ORDER BY ee.embedding <=> '{vec_str}'::vector
    LIMIT 5
    """

    result = subprocess.run(
        DOCKER_PSQL + ["-c", sql],
        capture_output=True, text=True, timeout=30,
    )

    log.info("Knowledge entity search (top 5):")
    for line in result.stdout.strip().split("\n"):
        log.info("  %s", line.strip())


def main():
    parser = argparse.ArgumentParser(description="Embed knowledge graph entities via Ollama")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-reindex", action="store_true")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Knowledge Graph Entity Embedding → knowledge.graph_entity_embeddings")
    log.info("Model: %s @ %s (dim=%d)", EMBED_MODEL, OLLAMA_URL, EMBED_DIM)
    log.info("=" * 60)

    entities = fetch_knowledge_entities()
    if not entities:
        log.error("No entities found")
        return 1

    lengths = [len(e["embed_text"]) for e in entities]
    log.info("Text stats: Min=%d Max=%d Mean=%.0f Median=%d",
             min(lengths), max(lengths),
             sum(lengths) / len(lengths),
             sorted(lengths)[len(lengths) // 2])

    start = time.time()
    embeddings = generate_embeddings(entities)
    elapsed = time.time() - start
    if embeddings:
        log.info("Embedding took %.1fs (%.2f s/entity)", elapsed, elapsed / len(entities))

    if not embeddings:
        log.error("No embeddings generated")
        return 1

    inserted = insert_embeddings(embeddings, dry_run=args.dry_run)

    if args.dry_run:
        return 0

    if not args.skip_reindex and inserted > 0:
        rebuild_index()

    run_semantic_test()

    log.info("=" * 60)
    log.info("COMPLETE: %d/%d knowledge entities embedded", inserted, len(entities))
    log.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
