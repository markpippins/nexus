#!/usr/bin/env bash
# bin/mongodb-health-monitor.sh — MongoDB health watcher
# ======================================================
#
# Monitors MongoDB liveness. When MongoDB transitions from DOWN → UP,
# restarts all services that depend on it (broker-gateway and other
# Spring Boot services with MongoRepository dependencies).
#
# Designed to be run as a systemd timer (mongodb-health-monitor.timer)
# every 30 seconds, or standalone for one-off checks.
#
# Usage
# -----
#     bin/mongodb-health-monitor.sh              # one cycle
#
# Exit codes
# ----------
#   0 — normal (no action needed or action completed)
#   1 — state file error

set -uo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXUS_ROOT="$(cd "$BIN_DIR/.." && pwd)"
STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}/nexus-monitor"
STATE_FILE="$STATE_DIR/mongodb-health-state.json"

# ── Configuration ──────────────────────────────────────────────────────

# Services that depend on MongoDB and need restarting when it recovers.
# broker-gateway hosts the Spring Boot service-broker components
# (search, user, note, admin-logging) that use MongoRepository.
MONGODB_DEPENDENT_SERVICES=(
    "broker-gateway.service"
)

# ── Authenticated readiness probe (MongoDB integration pass, 7b0d775d) ──
# HISTORICAL DEFECT: the probe below used `mongosh`/`mongo` on the HOST, but
# neither client is installed here — so every authenticated check silently
# fell through to a bare port check, and a mongod that was listening but
# refusing/failing auth still read as "healthy". This is the "health checks
# lie" finding: mongod deliberately answers an unauthenticated `ping`.
#
# Fix: authenticate. Run the read INSIDE the container (the mongo image
# ships the shell), against the admin auth DB, with credentials from the
# repo .env. `listCollections` on the app DB is a real read a bare ping
# cannot substitute for.
MONGO_CONTAINER="atomic-mongodb"
MONGO_APP_DB="nexus"
# Parse (do not source) .env — avoids executing arbitrary lines as shell.
_mongo_env() {
    local key="$1"
    local file="$NEXUS_ROOT/.env"
    [[ -f "$file" ]] || return 0
    sed -n "s/^${key}=//p" "$file" | tail -1
}
MONGO_ROOT_USER="$(_mongo_env MONGO_ROOT_USER)"; MONGO_ROOT_USER="${MONGO_ROOT_USER:-mongoUser}"
MONGO_ROOT_PASS="$(_mongo_env MONGO_ROOT_PASS)"; MONGO_ROOT_PASS="${MONGO_ROOT_PASS:-somePassword}"

# Authenticated readiness: true only when mongod accepts the credentials and
# completes a real read on the application database.
_mongodb_ready() {
    command -v docker &>/dev/null || return 1
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$MONGO_CONTAINER" || return 1
    local out
    out=$(timeout 8 docker exec "$MONGO_CONTAINER" mongo --quiet \
            -u "$MONGO_ROOT_USER" -p "$MONGO_ROOT_PASS" \
            --authenticationDatabase admin \
            --eval "db.getSiblingDB('${MONGO_APP_DB}').runCommand({listCollections:1}).ok" \
            2>/dev/null | tr -d '\r')
    [[ "$out" == *1* ]]
}

# Liveness only — port is open. Says NOTHING about auth or data reachability;
# used only as a labelled degraded fallback, never as the health verdict.
_mongodb_port_open() {
    ss -tlnp 2>/dev/null | grep -q ':27017 ' 2>/dev/null && return 0
    timeout 2 bash -c 'echo > /dev/tcp/localhost/27017' 2>/dev/null && return 0
    return 1
}

# ── Terrain registry write-back ────────────────────────────────────────
# MongoDB has no HTTP health endpoint and no heartbeat registration, so
# staleness sweeps periodically flip its terrain row OFFLINE while the
# service is healthy (incident 1f5c2806, 2026-08-23). This monitor already
# computes authoritative liveness every cycle — so we write the truth back.
TERRAIN_DSN="${PEB_DATABASE_URL:-postgresql://pguser:pgpass@localhost:5432/nexus}"
TERRAIN_SERVICE_NAME="mongodb"

_sync_terrain_status() {
    local is_up="$1"   # "true" / "false"
    local want="ONLINE"
    [[ "$is_up" == "false" ]] && want="OFFLINE"
    local current
    current=$(PGPASSWORD=pgpass PGUSER=pguser psql -h localhost -d nexus -t -A \
        -c "SELECT status FROM terrain.runnable_services WHERE name='${TERRAIN_SERVICE_NAME}' LIMIT 1;" 2>/dev/null)
    if [[ -z "$current" ]]; then
        _log "WARN" "Terrain row for '$TERRAIN_SERVICE_NAME' not found — skipping write-back"
        return 0
    fi
    if [[ "$current" != "$want" ]]; then
        PGPASSWORD=pgpass PGUSER=pguser psql -h localhost -d nexus -q \
            -c "UPDATE terrain.runnable_services SET status='${want}' WHERE name='${TERRAIN_SERVICE_NAME}' AND status <> '${want}';" 2>/dev/null
        _log "INFO" "Terrain status reconciled for $TERRAIN_SERVICE_NAME: ${current} -> ${want}"
    fi
}

# ── Helpers ─────────────────────────────────────────────────────────────

_log() {
    local level="$1"
    shift
    echo "[mongodb-health-monitor] $(date '+%Y-%m-%d %H:%M:%S') [$level] $*"
}

# Check if MongoDB is healthy.
#
# Verdict order:
#   1. authenticated readiness (authoritative) — auth + real read succeed
#   2. host-side client ping, if a client happens to exist (legacy path)
#   3. port-open LIVENESS ONLY — reported as degraded, never as healthy
#
# A mongod that is listening but failing auth therefore stays DOWN (honest),
# and the reason is logged so the failure is visible rather than masked.
_MONGODB_DEGRADED=false
_mongodb_healthy() {
    _MONGODB_DEGRADED=false
    # 1. Authoritative: authenticated read inside the container.
    if _mongodb_ready; then
        return 0
    fi
    # 2. Host client, if present (kept for portability; absent on this host).
    if command -v mongosh &>/dev/null; then
        if timeout 5 mongosh --quiet -u "$MONGO_ROOT_USER" -p "$MONGO_ROOT_PASS" \
            --authenticationDatabase admin --eval 'db.runCommand({ ping: 1 }).ok' \
            "mongodb://localhost:27017" 2>/dev/null | grep -q '1'; then
            return 0
        fi
    fi
    if command -v mongo &>/dev/null; then
        if timeout 5 mongo --quiet -u "$MONGO_ROOT_USER" -p "$MONGO_ROOT_PASS" \
            --authenticationDatabase admin --eval 'db.runCommand({ ping: 1 }).ok' \
            "mongodb://localhost:27017" 2>/dev/null | grep -q '1'; then
            return 0
        fi
    fi
    # 3. Degraded: port answers but readiness could not be proven.
    if _mongodb_port_open; then
        _MONGODB_DEGRADED=true
        _log "WARN" "mongod port 27017 is open but the authenticated readiness read FAILED — reporting degraded, not healthy (auth/credentials or data reachability problem)"
        return 0
    fi
    return 1
}

# Load previous state from JSON file.
# On first run (no state file), probe MongoDB and initialize state to match
# reality WITHOUT triggering a restart cycle.
_load_state() {
    if [[ -f "$STATE_FILE" ]]; then
        cat "$STATE_FILE" 2>/dev/null || echo '{"mongodb_was_up":false}'
    else
        if _mongodb_healthy; then
            echo '{"mongodb_was_up":true}'
        else
            echo '{"mongodb_was_up":false}'
        fi
    fi
}

# Save current state to JSON file.
_save_state() {
    local was_up="$1"
    mkdir -p "$STATE_DIR"
    cat > "$STATE_FILE" <<EOF
{
  "mongodb_was_up": $was_up,
  "last_checked": "$(date -Iseconds)"
}
EOF
}

# Restart a systemd user service.
_restart_service() {
    local svc="$1"
    _log "INFO" "Restarting $svc (MongoDB recovered)..."
    if systemctl --user restart "$svc" 2>/dev/null; then
        _log "INFO" "$svc restarted successfully"
        return 0
    else
        _log "WARN" "Failed to restart $svc — may need manual intervention"
        return 1
    fi
}

# ── Main ────────────────────────────────────────────────────────────────

main() {
    local state
    state=$(_load_state)

    # Extract previous MongoDB state
    local mongodb_was_up
    mongodb_was_up=$(echo "$state" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mongodb_was_up', False))" 2>/dev/null || echo "false")

    local mongodb_is_up=false
    if _mongodb_healthy; then
        mongodb_is_up=true
    fi

    # ── Transition: MongoDB was DOWN, now UP → restart dependent services ──
    if [[ "$mongodb_was_up" == "False" || "$mongodb_was_up" == "false" ]] && [[ "$mongodb_is_up" == "true" ]]; then
        _log "INFO" "MongoDB recovered (was down, now up) — restarting dependent services"

        for svc in "${MONGODB_DEPENDENT_SERVICES[@]}"; do
            _restart_service "$svc"
            sleep 1
        done

        _log "INFO" "All MongoDB-dependent services restarted"
    fi

    # ── Transition: MongoDB was UP, now DOWN → log warning ──
    if [[ "$mongodb_was_up" == "True" || "$mongodb_was_up" == "true" ]] && [[ "$mongodb_is_up" == "false" ]]; then
        _log "WARN" "MongoDB went DOWN — dependent services may be affected"
    fi

    # ── Steady state ──
    if [[ "$mongodb_is_up" == "true" ]]; then
        _log "DEBUG" "MongoDB is healthy"
    else
        _log "DEBUG" "MongoDB is DOWN"
    fi

    # ── Terrain registry write-back (liveness truth, every cycle) ──
    _sync_terrain_status "$mongodb_is_up"

    # Persist current state
    _save_state "$mongodb_is_up"
}

main "$@"
