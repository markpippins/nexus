#!/usr/bin/env bash
# bin/sysadmin-harness.sh — Sysadmin agent invocation harness
# ==========================================================
#
# Invokes the Sysadmin opencode agent for one maintenance cycle.
# Designed to be called by systemd (sysadmin-agent.service) on an
# hourly timer (sysadmin-agent.timer), or standalone.
#
# The harness:
#   1. Acquires a pidfile lock (prevents concurrent runs)
#   2. Sets up the environment (XDG_RUNTIME_DIR, etc.)
#   3. Resolves the model chain from tackle-srv (config/ai/resolve/sysadmin)
#   4. Calls `opencode run --agent sysadmin` with the cycle message,
#      advancing through the fallback chain on failure
#   5. Logs timing, exit code, and any stderr to journald AND nexus/logs/sysadmin-harness.log
#   6. Updates the incident log with the run outcome
#
# Usage
# -----
#     bin/sysadmin-harness.sh                       # one full cycle
#     bin/sysadmin-harness.sh --quick               # skip heavy checks, just ping + report
#     bin/sysadmin-harness.sh --outage "<service>"  # targeted run for a specific outage
#
# Exit codes
# ----------
#   0 — cycle completed normally
#   1 — lock contention (another run in progress)
#   2 — opencode not found
#   3 — cycle failed (opencode returned non-zero)

set -uo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXUS_ROOT="$(cd "$BIN_DIR/.." && pwd)"
STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}/nexus-sysadmin"
LOCK_FILE="$STATE_DIR/sysadmin-harness.lock"
LOG_FILE="$STATE_DIR/sysadmin-harness.log"

# Persistent audit log — nexus/logs (canonical, survives across sessions).
LOG_DIR="$NEXUS_ROOT/logs"
HARNESS_LOG="$LOG_DIR/sysadmin-harness.log"

# ── Config ──────────────────────────────────────────────────────────────

OPENCODE_BIN="${OPENCODE_BIN:-$(command -v opencode 2>/dev/null || echo "$HOME/.opencode/bin/opencode")}"
OPENCODE_PROJECT="${OPENCODE_PROJECT:-$NEXUS_ROOT}"
OPENCODE_AGENT="${OPENCODE_AGENT:-sysadmin}"
TIMEOUT_MINUTES="${TIMEOUT_MINUTES:-15}"  # max runtime per model attempt

# ── Model selection ────────────────────────────────────────────────────
# Primary source of truth: the tackle AI config registry (tackle-srv,
# port 3410) that tackle-ui writes to. The role's resolved model and its
# fallback chain come from GET /config/ai/resolve/<role>.
# SYSMODEL / FALLBACK_MODEL remain available as explicit overrides when a
# caller sets them, but are no longer baked into the systemd unit. Their values
# are opencode --model ids, inserted verbatim.
#
# CHAIN_BUDGET_MINUTES bounds the WHOLE ladder. A five-rung chain at the
# 15-minute per-attempt cap would run for over an hour and collide with the next
# hourly timer, so each attempt is capped at the remaining chain budget.
TACKLE_SRV_URL="${TACKLE_SRV_URL:-http://localhost:3410}"
ASSEMBLY_URL="${ASSEMBLY_URL:-http://localhost:3107}"
SYSMODEL="${SYSMODEL:-}"
FALLBACK_MODEL="${FALLBACK_MODEL:-}"
CHAIN_BUDGET_MINUTES="${CHAIN_BUDGET_MINUTES:-30}"

# ── Lock ────────────────────────────────────────────────────────────────

_acquire_lock() {
    mkdir -p "$STATE_DIR"
    if ! flock -n "$LOCK_FD" 2>/dev/null; then
        local pid
        pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "unknown")
        echo "[sysadmin-harness] $(date '+%Y-%m-%d %H:%M:%S') [LOCK] Another run is in progress (PID $pid) — deferring"
        exit 1
    fi
    echo "$$" > "$LOCK_FILE"
}

_release_lock() {
    exec {LOCK_FD}>&- 2>/dev/null || true
    rm -f "$LOCK_FILE" 2>/dev/null || true
}

# ── Helpers ─────────────────────────────────────────────────────────────

_log() {
    local level="$1"
    shift
    local line="[sysadmin-harness] $(date '+%Y-%m-%d %H:%M:%S') [$level] $*"
    # NOTE: log to stderr (captured by systemd/journald) so that stdout
    # stays clean — resolve_models() command-substitutes its output, and a
    # stdout echo would leak log lines into the model chain (the cause of
    # "Attempt 1" running with a garbage model ID and failing in ~35s).
    echo "$line" >&2
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    echo "$line" >> "$HARNESS_LOG" 2>/dev/null || true
}

# Check if opencode binary exists and is executable.
_check_opencode() {
    if [[ ! -x "$OPENCODE_BIN" ]]; then
        _log "ERROR" "opencode not found at $OPENCODE_BIN"
        _log "ERROR" "Set OPENCODE_BIN or install opencode"
        exit 2
    fi
}

# resolve_models <role> → newline-separated opencode model IDs, primary first.
#
# The ladder lives in bin/sysadmin_model_chain.py (pure functions, unit tested):
# tackle's primary pinned first, its fallback_models ordered by provider
# preference (nvidia → opencode → opencode-go → openrouter → ollama), duplicates
# collapsed, then a catalog tail so the role still has rungs when tackle is
# unreachable. This replaces the old inline chain, whose only tail was a single
# hardcoded opencode/big-pickle that then ran to the per-attempt cap.
#
# SYSMODEL / FALLBACK_MODEL are opencode --model ids and are inserted verbatim.
resolve_models() {
    local role="$1"
    local data=""
    if [[ -n "$SYSMODEL" ]]; then
        _log "INFO" "SYSMODEL override in effect: $SYSMODEL"
    else
        data=$(curl -s --max-time 10 "$TACKLE_SRV_URL/config/ai/resolve/$role" 2>/dev/null) || true
        if [[ -z "$data" ]]; then
            _log "WARN" "tackle resolve role=$role unavailable — catalog-only chain"
        else
            local primary fb_raw
            primary=$(printf '%s' "$data" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('model_identifier') or '')" 2>/dev/null || true)
            fb_raw=$(printf '%s' "$data" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for f in (d.get('fallback_models') or []):
        mid = f.get('model_identifier')
        if mid: print(mid)
except Exception:
    pass
" 2>/dev/null || true)
            _log "INFO" "tackle resolve role=$role primary=${primary:-<none>} fallbacks=[$(printf '%s' "$fb_raw" | tr '\n' ',')]"
        fi
    fi

    printf '%s' "$data" | python3 "$BIN_DIR/sysadmin_model_chain.py" \
        --role "$role" \
        --sysmodel "$SYSMODEL" \
        --fallback-model "$FALLBACK_MODEL"
}

# resolve_agent_user_id <role> → Assembly user UUID whose alias matches the
# role (e.g. "sysadmin"). The harness resolves identity deterministically so
# the agent never has to guess from GET /api/users — that lookup is what
# intermittently credited posts to the wrong user (e.g. Rover). Returns
# empty string if unresolvable (agent falls back to GET /api/users).
resolve_agent_user_id() {
    local role="$1"
    curl -s --max-time 10 "$ASSEMBLY_URL/api/users" 2>/dev/null \
        | python3 -c "
import sys, json
try:
    users = json.load(sys.stdin)
    for u in users:
        if (u.get('name') or '').lower() == sys.argv[1].lower():
            print(u.get('id') or '')
            break
except Exception:
    pass
" "$role" 2>/dev/null || true
}

# ── Main ────────────────────────────────────────────────────────────────

main() {
    local mode="full"
    local outage_target=""

    # Parse flags
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --quick)    mode="quick"; shift ;;
            --outage)   mode="outage"; outage_target="$2"; shift 2 ;;
            --help|-h)  sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# //'; exit 0 ;;
            *)          _log "WARN" "Unknown option: $1"; shift ;;
        esac
    done

    _check_opencode

    # Acquire lock (fd 200)
    exec {LOCK_FD}>"$LOCK_FILE"
    _acquire_lock
    trap _release_lock EXIT

    _log "INFO" "Starting sysadmin cycle (mode=$mode)"

    local start_ts
    start_ts=$(date +%s)

    # Build the message for the sysadmin agent based on mode
    local cycle_msg
    case "$mode" in
        full)
            cycle_msg="Execute hourly maintenance cycle: discover issues via terrain, act on them (restart down services, kill zombies), then post action summary to Assembly syslog heartbeat."
            ;;
        quick)
            cycle_msg="Quick health check — ping critical services and report. Skip terrain topology deep dive, skip zombie sweep."
            ;;
        outage)
            cycle_msg="Targeted response to detected outage: $outage_target. Determine root cause and act within authority ladder."
            ;;
    esac

    # Deterministic identity for the agent. The harness resolves the Assembly
    # user UUID here (never trust the agent to re-resolve — that produced the
    # intermittent "Rover" miscredit). The per-attempt model is exported as
    # NEXUS_AGENT_MODEL in the retry loop below.
    export NEXUS_AGENT_ROLE="$OPENCODE_AGENT"
    local agent_user_id
    agent_user_id=$(resolve_agent_user_id "$OPENCODE_AGENT")
    if [[ -n "$agent_user_id" ]]; then
        export NEXUS_AGENT_USER_ID="$agent_user_id"
    else
        unset NEXUS_AGENT_USER_ID
        _log "WARN" "Could not resolve Assembly user UUID for role $OPENCODE_AGENT — agent must fall back to GET /api/users"
    fi
    cycle_msg="${cycle_msg}

Your identity (injected by the harness — do not re-resolve): role=$NEXUS_AGENT_ROLE, Assembly user UUID=${NEXUS_AGENT_USER_ID:-<unresolved — fall back to GET /api/users matching name \"$OPENCODE_AGENT\">}, model=\$NEXUS_AGENT_MODEL (env, set per attempt). Every Assembly post/comment you make MUST include role and model in the request JSON and a footer line '— role: <role>, model: <model>'."

    # Run the sysadmin agent
    # We run with --format json to get machine-readable output, redirecting
    # stdin from /dev/null so the agent doesn't hang waiting for input.
    # The model chain comes from the tackle AI config registry (primary +
    # fallback_models), transformed to opencode model IDs; each failure
    # advances to the next candidate. Success/failure is per-candidate:
    # a non-zero opencode exit advances the chain.
    local model_list
    model_list=$(resolve_models "$OPENCODE_AGENT")
    _log "INFO" "Model chain: $(printf '%s' "$model_list" | tr '\n' ' > ')"

    local exit_code=1
    local output=""
    local attempt=1
    local model=""
    local last_fail=0
    local budget_s=$((CHAIN_BUDGET_MINUTES * 60))
    while IFS= read -r model; do
        [[ -n "$model" ]] || continue
        local remaining=$((budget_s - ($(date +%s) - start_ts)))
        if [[ "$remaining" -le 0 ]]; then
            _log "WARN" "chain budget ${CHAIN_BUDGET_MINUTES}m exhausted — skipping $model"
            continue
        fi
        local attempt_s=$((TIMEOUT_MINUTES * 60))
        [[ "$attempt_s" -gt "$remaining" ]] && attempt_s=$remaining
        _log "INFO" "Attempt $attempt: running with model $model (timeout=${attempt_s}s, chain budget=${CHAIN_BUDGET_MINUTES}m)"
        export NEXUS_AGENT_MODEL="$model"
        output=$(timeout "${attempt_s}s" "$OPENCODE_BIN" run \
            --agent "$OPENCODE_AGENT" \
            --model "$model" \
            --dir "$OPENCODE_PROJECT" \
            --format json \
            --dangerously-skip-permissions \
            "$cycle_msg" \
            </dev/null 2>&1) || last_fail=$?
        if [[ "$last_fail" -eq 0 ]]; then
            exit_code=0
            break
        fi
        _log "WARN" "Model $model failed (exit=$last_fail) — advancing to next candidate"
        exit_code=$last_fail
        attempt=$((attempt + 1))
        last_fail=0
    done <<< "$model_list"

    local end_ts
    end_ts=$(date +%s)
    local elapsed=$((end_ts - start_ts))

    _log "INFO" "Cycle completed in ${elapsed}s with exit code $exit_code"

    # Log the run outcome
    mkdir -p "$STATE_DIR"
    {
        echo "---"
        echo "cycle: $(date -Iseconds)"
        echo "mode: $mode"
        echo "exit_code: $exit_code"
        echo "elapsed_seconds: $elapsed"
        echo "---"
    } >> "$LOG_FILE"

    # Capture key output for the incident log
    if [[ "$exit_code" -ne 0 ]]; then
        _log "WARN" "opencode run returned non-zero exit code $exit_code"
        # Truncate output to last 50 lines for context
        echo "$output" | tail -50 >> "$LOG_FILE"
    fi

    # On outage mode, also append a brief to the incident log
    if [[ "$mode" == "outage" ]]; then
        echo "$output" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    summary = data.get('content', '')[:500] if isinstance(data, dict) else str(data)[:500]
    print(f'Outage response summary:\\n{summary}')
    print('\\n---')
except (json.JSONDecodeError, Exception):
    pass
" 2>/dev/null >> "$LOG_FILE" || true
    fi

    # Output a brief status line for journald
    echo "RESULT: mode=$mode exit=$exit_code elapsed=${elapsed}s"

    return $exit_code
}

# Run a cycle only when executed. Sourcing the file (smoke checks, tests) just
# defines the helpers — without this, any caller that sourced it launched a real
# agent cycle.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
