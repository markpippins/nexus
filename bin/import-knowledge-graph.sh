#!/usr/bin/env bash
# ============================================================================
# RETIRED (2026-09-02) — File → PostgreSQL graph ingest
#
# SUPERSEDED by the knowledge-srv write path (Option B). The knowledge graph is
# now written via the knowledge-srv REST API / knowledge-mcp wrapper directly,
# not from graph/nexus-knowledge-graph.json. That file is frozen as a
# historical generation; its WR/plan evidence is staged in Mongo `legacy-audit`.
# Retained for historical reference only — do NOT run as the canonical ingest.
# ============================================================================
# import-knowledge-graph.sh — Import the disk Knowledge Graph into PostgreSQL
#
# Full safe cycle for the disk-backed KG pipeline:
#
#   graph/nexus-knowledge-graph.json   (canonical disk state — edit this)
#        │  bin/import-knowledge-graph.sh
#        ▼
#   knowledge.graph_entities            (import via python/steward/migrate_graph.py)
#        │  (this script: expire stale canonical assets, purge orphan embeddings)
#        ▼
#   semantics.canonical_asset           (asset_id backfill via V083 migration SQL)
#        │  bin/embed-knowledge-graph.sh
#        ▼
#   knowledge.graph_entity_embeddings   (nomic-embed-text 768-dim, ivfflat index)
#
# WHY the extra cleanup steps exist (T24):
#   migrate_graph.py now UPSERTs entities (ON CONFLICT (section, entity_id) DO
#   UPDATE) and inserts edges with ON CONFLICT DO NOTHING — it never DELETEs
#   and preserves entity ids/asset_ids. Steps 2–3 below are therefore
#   defensive no-ops that clean up pre-existing orphans from the old
#   destructive delete-and-reinsert era, and keep the invariant:
#       active knowledge_entity assets == graph_entities rows == embeddings rows
#
# Usage:
#   bin/import-knowledge-graph.sh [--file graph/nexus-knowledge-graph.json]
#                                 [--skip-embed]   # import + backfill only
#                                 [--dry-run]      # report only, no writes
#   bin/import-knowledge-graph.sh --embed-args "--refresh"   # passthrough to embed
#
# Requires: rover venv (python/rover/.venv), pgvector_db container up,
#           Ollama :11434 with nomic-embed-text (unless --skip-embed).
# ============================================================================
set -euo pipefail

NEXUS_ROOT="/home/codex/dev/nexus"
VENV_PY="${NEXUS_ROOT}/python/rover/.venv/bin/python"
MIGRATE_SCRIPT="${NEXUS_ROOT}/python/steward/migrate_graph.py"
V083_SQL="${NEXUS_ROOT}/sql/V083__graph_entities_asset_id_backfill.sql"
EMBED_SCRIPT="${NEXUS_ROOT}/bin/embed-knowledge-graph.sh"
DEFAULT_FILE="${NEXUS_ROOT}/graph/nexus-knowledge-graph.json"
# migrate_graph.py default DSN is now fixed to pguser/pgpass@localhost:5432/nexus;
# keep the explicit override for deterministic pinning across shells:
export NEXUS_DB_DSN="${NEXUS_DB_DSN:-postgresql://pguser:pgpass@localhost:5432/nexus}"
PSQL=(docker exec -i pgvector_db psql -U pguser -d nexus -t -A -q)

FILE="${DEFAULT_FILE}"
SKIP_EMBED=0
DRY=0
EMBED_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) FILE="$2"; shift 2 ;;
    --skip-embed) SKIP_EMBED=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --embed-args) EMBED_ARGS+=("$2"); shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; exit 1 ;;
  esac
done

say() { echo "== $*"; }

# ── preflight ───────────────────────────────────────────────────────────────
if [[ ! -f "${FILE}" ]]; then
  echo "ERROR: KG file not found: ${FILE}" >&2; exit 1
fi
if [[ ! -x "${VENV_PY}" ]]; then
  echo "ERROR: rover venv missing: ${VENV_PY}" >&2; exit 1
fi
if ! docker ps --format '{{.Names}}' | grep -qx pgvector_db; then
  echo "ERROR: pgvector_db container not running" >&2; exit 1
fi
if [[ ! -f "${V083_SQL}" ]]; then
  echo "ERROR: V083 backfill SQL missing: ${V083_SQL}" >&2; exit 1
fi
if [[ "${SKIP_EMBED}" != "1" ]] && ! curl -sf --max-time 5 "${OLLAMA_URL:-http://127.0.0.1:11434}/api/tags" | grep -q nomic-embed-text; then
  echo "ERROR: Ollama unreachable or nomic-embed-text not pulled (or use --skip-embed)" >&2; exit 1
fi

say "KG import pipeline"
say "  file:   ${FILE}"
say "  dry-run: $([ ${DRY} == 1 ] && echo yes || echo no)"
say "  embed:  $([ ${SKIP_EMBED} == 1 ] && echo skipped || echo 'yes (after backfill)')"

before=$("${PSQL[@]}" -c "SELECT (SELECT count(*) FROM knowledge.graph_entities) || ' entities / ' || (SELECT count(*) FROM knowledge.graph_entity_embeddings) || ' emb / ' || (SELECT count(*) FROM semantics.canonical_asset WHERE asset_kind='knowledge_entity' AND expired_at IS NULL) || ' knowledge_assets'")
say "BEFORE: ${before}"

# ── 1. import (lossless idempotent upsert from JSON) ───────────────────────
MIGRATE_ARGS=(--file "${FILE}")
if [[ "${DRY}" == "1" ]]; then
  MIGRATE_ARGS+=(--dry-run)
fi
say "step 1/4: migrate_graph.py ${MIGRATE_ARGS[*]}"
"${VENV_PY}" "${MIGRATE_SCRIPT}" "${MIGRATE_ARGS[@]}"

if [[ "${DRY}" == "1" ]]; then
  say "dry-run complete — no writes performed"
  exit 0
fi

# ── 2. expire stale knowledge_entity canonical assets (defensive no-op) ────
say "step 2/4: expire stale knowledge_entity canonical assets (no longer referenced)"
"${PSQL[@]}" -c "
UPDATE semantics.canonical_asset ca
SET expired_at = now()
WHERE ca.asset_kind = 'knowledge_entity'
  AND ca.expired_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM knowledge.graph_entities ge WHERE ge.asset_id = ca.id
  );"

# ── 3. purge orphan embeddings (defensive no-op) + V083 asset_id backfill ──
say "step 3/4: purge orphan embeddings + run V083 asset_id backfill"
"${PSQL[@]}" -c "
DELETE FROM knowledge.graph_entity_embeddings
WHERE entity_id NOT IN (SELECT id FROM knowledge.graph_entities);"
docker exec -i pgvector_db psql -U pguser -d nexus -v ON_ERROR_STOP=1 -f - < "${V083_SQL}"

# ── 4. embed (backfill missing only) ───────────────────────────────────────
if [[ "${SKIP_EMBED}" == "1" ]]; then
  say "step 4/4: embed skipped (--skip-embed) — run bin/embed-knowledge-graph.sh later"
else
  say "step 4/4: embed-knowledge-graph.sh ${EMBED_ARGS[*]:-}"
  if (( ${#EMBED_ARGS[@]} )); then
    "${EMBED_SCRIPT}" "${EMBED_ARGS[@]}"
  else
    "${EMBED_SCRIPT}"
  fi
fi

# ── verify invariants ──────────────────────────────────────────────────────
after=$("${PSQL[@]}" -c "
SELECT (SELECT count(*) FROM knowledge.graph_entities) || ' entities / ' ||
       (SELECT count(*) FROM knowledge.graph_entity_embeddings) || ' emb / ' ||
       (SELECT count(*) FROM semantics.canonical_asset WHERE asset_kind='knowledge_entity' AND expired_at IS NULL) || ' knowledge_assets / ' ||
       (SELECT count(*) FROM knowledge.graph_entities WHERE asset_id IS NULL) || ' missing_asset / ' ||
       (SELECT count(*) FROM knowledge.graph_entity_embeddings e WHERE NOT EXISTS (SELECT 1 FROM knowledge.graph_entities g WHERE g.id = e.entity_id)) || ' orphan_emb'")
say "AFTER: ${after}"
say "DONE — pipeline complete"
