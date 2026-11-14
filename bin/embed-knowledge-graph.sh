#!/usr/bin/env bash
# ============================================================================
# embed-knowledge-graph.sh — Re-embed Knowledge Graph entities (Layer A)
#
# After a KG rebuild + re-import into Postgres (knowledge.graph_entities), the
# rows in knowledge.graph_entity_embeddings go stale or empty. This script
# backfills them via Ollama nomic-embed-text (768-dim) using the shared rover
# venv and bin/embed_semantic_layers.py, then creates the ivfflat cosine index
# that knowledge.semantic_search() expects.
#
# Usage:
#   bin/embed-knowledge-graph.sh                # backfill missing embeddings
#   bin/embed-knowledge-graph.sh --refresh      # TRUNCATE table first, full re-embed
#   bin/embed-knowledge-graph.sh --dry-run      # report only (no writes; still runs
#                                               #   inference — pair with --limit for a quick check)
#   bin/embed-knowledge-graph.sh --limit 100    # cap rows (smoke test)
#
# Watch progress: tail -f logs/embed_semantic_layers.log
# Requires: local Ollama (:11434) with nomic-embed-text pulled, pgvector_db up.
# ============================================================================
set -euo pipefail

NEXUS_ROOT="/home/codex/dev/nexus"
VENV_PY="${NEXUS_ROOT}/python/rover/.venv/bin/python"
EMBED_SCRIPT="${NEXUS_ROOT}/bin/embed_semantic_layers.py"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
MODEL="nomic-embed-text"
PSQL=(docker exec -i pgvector_db psql -U pguser -d nexus -t -A -q)

REFRESH=0
DRY=0
PASSTHRU=()
for a in "$@"; do
  case "$a" in
    --refresh) REFRESH=1 ;;
    --dry-run) DRY=1 ;;
    *) PASSTHRU+=("$a") ;;
  esac
done

say() { echo "== $*"; }

say "KG entity embedding - ${MODEL} @ ${OLLAMA_URL}"

# ── preflight ───────────────────────────────────────────────────────────────
if ! curl -sf --max-time 5 "${OLLAMA_URL}/api/tags" | grep -q "${MODEL}"; then
  echo "ERROR: Ollama unreachable or model '${MODEL}' not pulled (run: ollama pull ${MODEL})" >&2
  exit 1
fi
if [[ ! -x "${VENV_PY}" ]]; then
  echo "ERROR: rover venv missing: ${VENV_PY}" >&2
  exit 1
fi
if ! docker ps --format '{{.Names}}' | grep -qx pgvector_db; then
  echo "ERROR: pgvector_db container not running" >&2
  exit 1
fi

counts=$("${PSQL[@]}" -c "SELECT (SELECT count(*) FROM knowledge.graph_entities) || ' entities / ' || (SELECT count(*) FROM knowledge.graph_entity_embeddings) || ' embedded'")
say "DB: ${counts}"

# ── optional full refresh ───────────────────────────────────────────────────
if [[ "${REFRESH}" == "1" ]]; then
  if [[ "${DRY}" == "1" ]]; then
    echo "(--refresh ignored in dry-run)"
  else
    say "--refresh: TRUNCATE knowledge.graph_entity_embeddings (if this run dies mid-way, the table stays empty until re-run)"
    "${PSQL[@]}" -c "TRUNCATE knowledge.graph_entity_embeddings;"
  fi
fi

# ── embed (Layer A = KG entities) ───────────────────────────────────────────
ARGS=(--layers A)
if [[ "${DRY}" == "1" ]]; then
  ARGS+=(--dry-run)
else
  ARGS+=(--commit)
fi
if (( ${#PASSTHRU[@]} )); then
  ARGS+=("${PASSTHRU[@]}")
fi
say "running bin/embed_semantic_layers.py ${ARGS[*]}"
"${VENV_PY}" "${EMBED_SCRIPT}" "${ARGS[@]}"

# ── index + verify (skipped in dry-run) ────────────────────────────────────
if [[ "${DRY}" == "0" ]]; then
  say "ensuring ivfflat cosine index (idx_kg_entity_embeddings_ivfflat)"
  "${PSQL[@]}" -c "CREATE INDEX IF NOT EXISTS idx_kg_entity_embeddings_ivfflat
                   ON knowledge.graph_entity_embeddings
                   USING ivfflat (embedding vector_cosine_ops) WITH (lists = 16);"
  final=$("${PSQL[@]}" -c "SELECT (SELECT count(*) FROM knowledge.graph_entities) || ' entities / ' || (SELECT count(*) FROM knowledge.graph_entity_embeddings) || ' embedded'")
  say "DONE: ${final}"
else
  say "dry-run complete - no writes performed"
fi
