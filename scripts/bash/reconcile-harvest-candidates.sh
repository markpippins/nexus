#!/usr/bin/env bash
#
# reconcile-harvest-candidates.sh
#
# PURPOSE:
#   Runs the full three-path reconciliation pipeline for harvest candidates.
#   This script identifies which uncompleted harvest candidates correspond to
#   work that has already been finished, and marks them as completed in the
#   PostgreSQL database (nexus schema).
#
# THREE RECONCILIATION PATHS:
#   1. KEYWORD MATCHING  — reconciles candidates against completed WorkRequest
#      plans (REVIEW_PASS status) using keyword/sparse matching.
#   2. EMBEDDING MATCHING (PLANS) — uses Ollama embedding model
#      (snowflake-arctic-embed2) to compute dense semantic similarity between
#      candidate intent and completed plan text (including DCO execution
#      evidence). Finds matches that keyword matching misses.
#   3. EMBEDDING MATCHING (AGENT RECORDS) — uses the same embedding model to
#      match candidates against finalized agent records (engineer logs,
#      builder logs, architect decisions/reports). Finds work completed
#      *outside* the formal WorkRequest pipeline.
#
# PREREQUISITES:
#   - Docker container `pgvector_db` running with the nexus database
#   - Ollama running locally (127.0.0.1):11434 with `snowflake-arctic-embed2` pulled
#   - Python venv at nexus/python/rover/.venv activated (or the script will
#     activate it for you)
#   - Nebula REST API reachable at localhost:3101 (for --apply via API)
#
# USAGE:
#   cd ~/dev/nexus/scripts
#   ./reconcile-harvest-candidates.sh
#
#   The script defaults to --dry-run for all three paths. To actually apply
#   updates, edit the APPLY_MODE variable below to "true".
#
#   Each path writes its match results to /tmp for review:
#     /tmp/reconcile_keyword.json
#     /tmp/reconcile_embed.json
#     /tmp/reconcile_agent.json
#
# OUTPUT:
#   - Console logs show candidate counts, match counts, confidence breakdowns
#   - JSON files contain full matched/unmatched lists for human review
#   - Database is updated only if APPLY_MODE=true
#
# SAFETY:
#   - The script defaults to dry-run. Set APPLY_MODE=true to write changes.
#   - Each Python script now uses direct SQL UPDATE (bypassing the Nebula API).
#   - This shell script provides an ADDITIONAL SQL fallback step that reads the
#     JSON output files and applies any matched IDs via direct psql, ensuring
#     no candidate is missed even if a Python script exits abnormally.
#
# AUTHOR:  generated via Codebuff assistant
# DATE:    2026-07-02
#

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────

# Set to "true" to actually mark candidates as completed.
# "false" runs all three paths in --dry-run mode (safe default).
APPLY_MODE="false"

# Paths
ROVER_DIR="/home/codex/dev/nexus/python/rover"
BIN_DIR="/home/codex/dev/nexus/bin"
VENV_ACTIVATE="${ROVER_DIR}/.venv/bin/activate"
OUTPUT_DIR="/tmp"

# ── Helper functions ───────────────────────────────────────────────────

log_info()  { echo "[INFO]  $*"; }
log_warn()  { echo "[WARN]  $*" >&2; }
log_error() { echo "[ERROR] $*" >&2; }

separator() { echo "========================================"; }

check_prereqs() {
    log_info "Checking prerequisites..."

    # Check Docker / pgvector_db
    if ! docker ps --format '{{.Names}}' | grep -qx "pgvector_db"; then
        log_error "Docker container 'pgvector_db' is not running."
        log_error "Start it with: docker start pgvector_db"
        exit 1
    fi
    log_info "  ✓ pgvector_db container is running"

    # Check Ollama
          OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
      if ! curl -sf "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
          log_error "Ollama is not reachable at ${OLLAMA_URL}."
        log_error "Start it with: ollama serve"
        exit 1
    fi
    log_info "  ✓ Ollama is reachable"

    # Check embedding model
          if ! curl -sf "${OLLAMA_URL}/api/tags" | grep -q "snowflake-arctic-embed2"; then
        log_warn "  Model 'snowflake-arctic-embed2' not found in Ollama."
        log_warn "  Pull it with: ollama pull snowflake-arctic-embed2"
    else
        log_info "  ✓ snowflake-arctic-embed2 model is available"
    fi

    # Check Python venv
    if [[ ! -f "$VENV_ACTIVATE" ]]; then
        log_error "Python venv not found at ${VENV_ACTIVATE}"
        exit 1
    fi
    log_info "  ✓ Python venv exists"
}

run_python_script() {
    local script_name="$1"
    local extra_args="${2:-}"
    local output_file="${3:-}"
    local dry_run_flag=""

    if [[ "$APPLY_MODE" != "true" ]]; then
        dry_run_flag="--dry-run"
    fi

    log_info "Running ${script_name} ..."
    if [[ -n "$output_file" ]]; then
        python3 "${BIN_DIR}/${script_name}" \
            ${dry_run_flag} \
            ${extra_args} \
            --output "$output_file" \
            2>&1 | tee "${output_file%.json}.log"
    else
        python3 "${BIN_DIR}/${script_name}" \
            ${dry_run_flag} \
            ${extra_args} \
            2>&1
    fi
}

print_summary() {
    local file="$1"
    local label="$2"

    if [[ ! -f "$file" ]]; then
        log_warn "No output file for ${label}"
        return
    fi

    local matched
    matched=$(python3 -c "
import json,sys
try:
    d=json.load(open('$file'))
    print(len(d.get('matched',[])))
except Exception as e:
    print('ERR')
" 2>/dev/null)

    local unmatched
    unmatched=$(python3 -c "
import json,sys
try:
    d=json.load(open('$file'))
    print(len(d.get('unmatched',[])))
except Exception as e:
    print('ERR')
" 2>/dev/null)

    separator
    log_info "${label} SUMMARY"
    log_info "  Matched:   ${matched}"
    log_info "  Unmatched: ${unmatched}"
    log_info "  Output:    ${file}"
    separator
}

sql_fallback_from_json() {
    local json_file="$1"
    local label="$2"

    if [[ ! -f "$json_file" ]]; then
        log_warn "No JSON file for SQL fallback: ${json_file}"
        return
    fi

    local ids
    ids=$(python3 -c "
import json,sys
try:
    d=json.load(open('$json_file'))
    ids=[m['candidate_id'] for m in d.get('matched',[])]
    print(','.join(\"'\"+i+\"'\" for i in ids))
except Exception as e:
    print('')
" 2>/dev/null)

    if [[ -z "$ids" ]]; then
        log_info "SQL fallback: no matched IDs in ${label}"
        return
    fi

    log_info "SQL fallback for ${label}: applying matched IDs directly..."
    local sql="UPDATE nebula.harvest_candidates SET completed = true WHERE id IN (${ids});"
    local result
    if result=$(timeout 30 docker exec -i pgvector_db psql -U pguser -d nexus -t -A -c "$sql" 2>&1); then
        log_info "SQL fallback result: ${result}"
    else
        log_warn "SQL fallback failed (non-fatal): ${result}"
    fi
}

# ── Main ───────────────────────────────────────────────────────────────

main() {
    separator
    log_info "Harvest Candidate Reconciliation Pipeline"
    log_info "APPLY_MODE = ${APPLY_MODE}"
    log_info "START_TIME = $(date -Iseconds)"
    separator
    echo ""

    check_prereqs
    echo ""

    # Activate Python venv
    source "$VENV_ACTIVATE"
    log_info "Activated venv: ${ROVER_DIR}/.venv"
    echo ""

    # ── Path 1: Keyword matching against completed plans ───────────────
    separator
    log_info "PATH 1/3: Keyword matching against completed WorkRequest plans"
    separator
    run_python_script \
        "reconcile_completed.py" \
        "" \
        "${OUTPUT_DIR}/reconcile_keyword.json"
    print_summary "${OUTPUT_DIR}/reconcile_keyword.json" "Path 1 (Keyword)"
    if [[ "$APPLY_MODE" == "true" ]]; then
        sql_fallback_from_json "${OUTPUT_DIR}/reconcile_keyword.json" "Path 1 (Keyword)"
    fi
    echo ""

    # ── Path 2: Embedding matching against completed plans ─────────────
    separator
    log_info "PATH 2/3: Embedding semantic matching against completed plans"
    log_info "  (This may take 5–10 minutes on first run while Ollama generates"
    log_info "   embeddings; subsequent runs use the local cache and are fast.)"
    separator
    run_python_script \
        "reconcile_embeddings.py" \
        "" \
        "${OUTPUT_DIR}/reconcile_embed.json"
    print_summary "${OUTPUT_DIR}/reconcile_embed.json" "Path 2 (Embedding → Plans)"
    if [[ "$APPLY_MODE" == "true" ]]; then
        sql_fallback_from_json "${OUTPUT_DIR}/reconcile_embed.json" "Path 2 (Embedding → Plans)"
    fi
    echo ""

    # ── Path 3: Embedding matching against agent records ───────────────
    separator
    log_info "PATH 3/3: Embedding semantic matching against agent records"
    log_info "  (Finds work completed outside the formal WorkRequest flow.)"
    separator
    run_python_script \
        "reconcile_agent_records.py" \
        "" \
        "${OUTPUT_DIR}/reconcile_agent.json"
    print_summary "${OUTPUT_DIR}/reconcile_agent.json" "Path 3 (Embedding → Agent Records)"
    if [[ "$APPLY_MODE" == "true" ]]; then
        sql_fallback_from_json "${OUTPUT_DIR}/reconcile_agent.json" "Path 3 (Embedding → Agent Records)"
    fi
    echo ""

    # ── Final summary ──────────────────────────────────────────────────
    separator
    log_info "PIPELINE COMPLETE"
    log_info "END_TIME = $(date -Iseconds)"
    log_info ""
    log_info "Review the JSON outputs in ${OUTPUT_DIR}:"
    log_info "  ${OUTPUT_DIR}/reconcile_keyword.json"
    log_info "  ${OUTPUT_DIR}/reconcile_embed.json"
    log_info "  ${OUTPUT_DIR}/reconcile_agent.json"
    log_info ""
    if [[ "$APPLY_MODE" != "true" ]]; then
        log_info "All runs were in DRY-RUN mode."
        log_info "To apply changes, set APPLY_MODE=\"true\" at the top of this script."
    fi
    separator
}

# Run main
main "$@"
