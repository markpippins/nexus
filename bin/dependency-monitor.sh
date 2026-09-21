#!/usr/bin/env bash
# bin/dependency-monitor.sh — unified dependency-aware service monitor
# ====================================================================
#
# Monitors critical infrastructure services and restarts dependents when
# each recovers from a DOWN state.
#
# Currently monitors:
#   Infrastructure (Docker): Redis, MongoDB, PostgreSQL, NATS
#   System services:        service-registry, terrain, cascade-srv, peb-kernel
#
# Designed to be run as a systemd timer (dependency-monitor.timer)
# every 30 seconds, or standalone for one-off checks.
#
# Usage
# -----
#     bin/dependency-monitor.sh              # one cycle
#
# Exit codes
# ----------
#   0 — normal (no action needed or action completed)

set -uo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXUS_ROOT="$(cd "$BIN_DIR/.." && pwd)"
STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}/nexus-monitor"
STATE_FILE="$STATE_DIR/dependency-state.json"

# ── Dependency Map ─────────────────────────────────────────────────────
#
# Each infrastructure service has:
#   - A health check function
#   - A list of dependent systemd services to restart on recovery
#
# Add new infrastructure services by adding a new section below.

# ── Redis (port 6379) ──────────────────────────────────────────────────
# Services that Wants=redis.service in their systemd units.

REDIS_DEPENDENT_SERVICES=(
    "service-registry.service"
    "role-memory-srv.service"
    "tackle-srv.service"
    "tackle-mcp.service"
    "cascade-event-bridge.service"
    "cascade-pg-bridge.service"
)

_redis_healthy() {
    # Try redis-cli ping first (with 3s timeout)
    if command -v redis-cli &>/dev/null; then
        local result
        result=$(timeout 3 redis-cli -h localhost -p 6379 ping 2>/dev/null)
        if [[ "$result" == "PONG" ]]; then
            return 0
        fi
    fi
    # Fallback: check if port 6379 is listening
    ss -tlnp 2>/dev/null | grep -q ':6379 ' 2>/dev/null && return 0
    # Fallback: /dev/tcp bash built-in
    timeout 2 bash -c 'echo > /dev/tcp/localhost/6379' 2>/dev/null && return 0
    return 1
}

# ── MongoDB (port 27017) ───────────────────────────────────────────────
# broker-gateway hosts Spring Boot components that use MongoRepository.
# No formal systemd dependency, but these services break if Mongo is down.

MONGODB_DEPENDENT_SERVICES=(
    "broker-gateway.service"
)

_mongodb_healthy() {
    # Method 1: mongosh ping (with 5s timeout)
    if command -v mongosh &>/dev/null; then
        if timeout 5 mongosh --quiet --eval 'db.runCommand({ ping: 1 }).ok' "mongodb://localhost:27017" 2>/dev/null | grep -q '1'; then
            return 0
        fi
    fi
    # Method 2: mongo ping (with 5s timeout)
    if command -v mongo &>/dev/null; then
        if timeout 5 mongo --quiet --eval 'db.runCommand({ ping: 1 }).ok' "mongodb://localhost:27017" 2>/dev/null | grep -q '1'; then
            return 0
        fi
    fi
    # Method 3: Docker container check
    if command -v docker &>/dev/null; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'atomic-mongodb'; then
            timeout 2 bash -c 'echo > /dev/tcp/localhost/27017' 2>/dev/null && return 0
        fi
    fi
    # Method 4: port check
    ss -tlnp 2>/dev/null | grep -q ':27017 ' 2>/dev/null && return 0
    # Method 5: /dev/tcp
    timeout 2 bash -c 'echo > /dev/tcp/localhost/27017' 2>/dev/null && return 0
    return 1
}

# ── PostgreSQL (port 5432) —————————————————————————————————————————————
# The most depended-upon infrastructure. Most TypeScript/Go services
# handle DB reconnect gracefully; Java/JPA services (Hibernate) are
# more likely to need a restart due to stale connection pools.
#
# We restart a curated subset rather than all 24+ Postgres dependents,
# since a blanket restart would disrupt the entire system.

POSTGRESQL_DEPENDENT_SERVICES=(
    # Java/JPA services — most likely to need restart on DB reconnect
    "service-registry.service"
    "broker-gateway.service"
    "peb-kernel.service"
    # Infrastructure that tracks DB state
    "nebula-srv.service"
    "conduit-mcp.service"
    "conduit-srv.service"
    "cpf-api.service"
    "execution-srv.service"
    "cascade-srv.service"
    # Tackle stack — AI config registry backed by tackle schema
    "tackle-srv.service"
    "tackle-mcp.service"
    # Wind — database-backed API server
    "wind-srv.service"
)

_postgresql_healthy() {
    # Method 1: pg_isready (standard PostgreSQL health check)
    if command -v pg_isready &>/dev/null; then
        if pg_isready -h localhost -p 5432 2>/dev/null | grep -q 'accepting connections'; then
            return 0
        fi
    fi
    # Method 2: psql ping
    if command -v psql &>/dev/null; then
        if timeout 5 psql -h localhost -p 5432 -U pguser -d nexus -c 'SELECT 1' 2>/dev/null | grep -q '1'; then
            return 0
        fi
    fi
    # Method 3: Docker container check (container is named pgvector_db)
    if command -v docker &>/dev/null; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -qE '^(dev-postgres|nexus-postgres|atomic-postgres|pgvector_db)$'; then
            timeout 2 bash -c 'echo > /dev/tcp/localhost/5432' 2>/dev/null && return 0
        fi
    fi
    # Method 4: port check
    ss -tlnp 2>/dev/null | grep -q ':5432 ' 2>/dev/null && return 0
    # Method 5: /dev/tcp
    timeout 2 bash -c 'echo > /dev/tcp/localhost/5432' 2>/dev/null && return 0
    return 1
}

# ── Registration ───────────────────────────────────────────────────────
# This table tells the main loop which services to check and what
# to restart when they recover.

declare -A SERVICE_CHECKERS
declare -A SERVICE_DEPENDENTS
declare -A SERVICE_NAMES

_register_service() {
    local key="$1"        # e.g. "redis"
    local name="$2"        # Display name e.g. "Redis"
    local check_fn="$3"    # Function name to call for health check
    local -n deps_ref="$4" # Array name of dependent services

    SERVICE_NAMES[$key]="$name"
    SERVICE_CHECKERS[$key]="$check_fn"
    # Store dependents as a semicolon-separated string
    local joined=""
    for dep in "${deps_ref[@]}"; do
        [ -n "$joined" ] && joined="$joined;"
        joined="${joined}${dep}"
    done
    SERVICE_DEPENDENTS[$key]="$joined"
}

# ── NATS (port 4222) ───────────────────────────────────────────────────
# Messaging backbone. Cascade subscribers and TTS connect via NATS.

NATS_DEPENDENT_SERVICES=(
    "cascade-kernel-subscriber.service"
    "cascade-obs-subscriber.service"
    "address-tts.service"
)

_nats_healthy() {
    # Method 1: Docker container check
    if command -v docker &>/dev/null; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'nats'; then
            timeout 2 bash -c 'echo > /dev/tcp/localhost/4222' 2>/dev/null && return 0
        fi
    fi
    # Method 2: port check
    ss -tlnp 2>/dev/null | grep -q ':4222 ' 2>/dev/null && return 0
    # Method 3: /dev/tcp
    timeout 2 bash -c 'echo > /dev/tcp/localhost/4222' 2>/dev/null && return 0
    return 1
}

# ── Service Registry (port 8085) ───────────────────────────────────────
# SSE health stream + service discovery. Critical for health monitoring.

SERVICE_REGISTRY_DEPENDENT_SERVICES=(
    "broker-gateway.service"
)

_service_registry_healthy() {
    # Method 1: HTTP health via /api/v1/status/stream/clients (lightweight)
    if command -v curl &>/dev/null; then
        if curl -s --max-time 3 http://localhost:8085/api/v1/status/stream/clients >/dev/null 2>&1; then
            return 0
        fi
    fi
    # Method 2: port check
    ss -tlnp 2>/dev/null | grep -q ':8085 ' 2>/dev/null && return 0
    # Method 3: /dev/tcp
    timeout 2 bash -c 'echo > /dev/tcp/localhost/8085' 2>/dev/null && return 0
    return 1
}

# ── Terrain (port 8084) ───────────────────────────────────────────────
# Topology registry. MCP + heartbeat services depend on it.

TERRAIN_DEPENDENT_SERVICES=(
    "terrain-mcp.service"
    "heartbeat-terrain.service"
)

_terrain_healthy() {
    # Method 1: HTTP health probe at /api/v1/platform/health (actual terrain endpoint)
    if command -v curl &>/dev/null; then
        if curl -s --max-time 3 http://localhost:8084/api/v1/platform/health >/dev/null 2>&1; then
            return 0
        fi
    fi
    # Method 2: port check
    ss -tlnp 2>/dev/null | grep -q ':8084 ' 2>/dev/null && return 0
    # Method 3: /dev/tcp
    timeout 2 bash -c 'echo > /dev/tcp/localhost/8084' 2>/dev/null && return 0
    return 1
}

# ── Cascade-srv (port 3106) ───────────────────────────────────────────
# Cascade Event API. Heartbeat and subscribers depend on it.

CASCADE_SRV_DEPENDENT_SERVICES=(
    "heartbeat-cascade-srv.service"
)

_cascade_srv_healthy() {
    # Port check (it's a TypeScript HTTP server)
    ss -tlnp 2>/dev/null | grep -q ':3106 ' 2>/dev/null && return 0
    timeout 2 bash -c 'echo > /dev/tcp/localhost/3106' 2>/dev/null && return 0
    return 1
}

# ── PEB-kernel (port 8098) ────────────────────────────────────────────
# Engineering brain. Heartbeat services depend on it.

PEB_KERNEL_DEPENDENT_SERVICES=(
    "heartbeat-peb-kernel.service"
)

_peb_kernel_healthy() {
    # Method 1: HTTP health probe (Spring Boot actuator if available)
    if command -v curl &>/dev/null; then
        if curl -s --max-time 3 http://localhost:8098/actuator/health >/dev/null 2>&1 || \
           curl -s --max-time 3 http://localhost:8098/health >/dev/null 2>&1; then
            return 0
        fi
    fi
    ss -tlnp 2>/dev/null | grep -q ':8098 ' 2>/dev/null && return 0
    timeout 2 bash -c 'echo > /dev/tcp/localhost/8098' 2>/dev/null && return 0
    return 1
}

# ── Registration ───────────────────────────────────────────────────────

declare -A SERVICE_CHECKERS
declare -A SERVICE_DEPENDENTS
declare -A SERVICE_NAMES

_register_service() {
    local key="$1"        # e.g. "redis"
    local name="$2"        # Display name e.g. "Redis"
    local check_fn="$3"    # Function name to call for health check
    local -n deps_ref="$4" # Array name of dependent services

    SERVICE_NAMES[$key]="$name"
    SERVICE_CHECKERS[$key]="$check_fn"
    local joined=""
    for dep in "${deps_ref[@]}"; do
        [ -n "$joined" ] && joined="$joined;"
        joined="${joined}${dep}"
    done
    SERVICE_DEPENDENTS[$key]="$joined"
}

_register_service "redis"          "Redis"          "_redis_healthy"          REDIS_DEPENDENT_SERVICES
_register_service "mongodb"        "MongoDB"        "_mongodb_healthy"        MONGODB_DEPENDENT_SERVICES
_register_service "postgresql"     "PostgreSQL"     "_postgresql_healthy"     POSTGRESQL_DEPENDENT_SERVICES
_register_service "nats"           "NATS"           "_nats_healthy"           NATS_DEPENDENT_SERVICES
_register_service "service-registry" "Service-Registry" "_service_registry_healthy" SERVICE_REGISTRY_DEPENDENT_SERVICES
_register_service "terrain"        "Terrain"        "_terrain_healthy"        TERRAIN_DEPENDENT_SERVICES
_register_service "cascade-srv"    "Cascade-srv"    "_cascade_srv_healthy"    CASCADE_SRV_DEPENDENT_SERVICES
_register_service "peb-kernel"     "PEB-kernel"     "_peb_kernel_healthy"     PEB_KERNEL_DEPENDENT_SERVICES

# ── Helpers ─────────────────────────────────────────────────────────────

_log() {
    local level="$1"
    shift
    echo "[dependency-monitor] $(date '+%Y-%m-%d %H:%M:%S') [$level] $*"
}

# Load previous state from JSON file, merging in any new infrastructure
# services that have been registered since the state file was written.
#
# On first run (no state file), probe all services and initialize state
# to match reality WITHOUT triggering a restart cycle.
#
# When new services are added to the monitor (e.g. adding NATS to a
# state file that only had Redis/MongoDB/PostgreSQL), their previous
# state defaults to their CURRENT health status so no false DOWN→UP
# transition fires on upgrade.
_load_state() {
    if [[ -f "$STATE_FILE" ]]; then
        # Read existing state
        local existing
        existing=$(cat "$STATE_FILE" 2>/dev/null || echo '{}')

        # Check if any registered services are MISSING from the state
        local missing=false
        for key in "${!SERVICE_CHECKERS[@]}"; do
            if ! echo "$existing" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('${key}_was_up', 'MISSING'))
" 2>/dev/null | grep -qv 'MISSING'; then
                missing=true
                break
            fi
        done

        if ! $missing; then
            # All keys present — return state as-is
            echo "$existing"
            return
        fi

        # Merge: for each registered service missing from the state,
        # probe its current health and add it.
        local merged
        # Remove trailing } to prepare for appending
        merged=$(echo "$existing" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.dumps(d, indent=2)[:-1])
" 2>/dev/null || echo '{')
        # If merged is just '{', the original was empty or corrupt
        [[ "$merged" == "{" ]] && merged='{'
        # Append a comma separator if merged already has content after the opening brace
        if echo "$merged" | grep -qEv '^\{$'; then
            merged+=",\n"
        fi
        local added_count=0
        for key in "${!SERVICE_CHECKERS[@]}"; do
            if echo "$existing" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('${key}_was_up', 'MISSING'))
" 2>/dev/null | grep -qv 'MISSING'; then
                continue
            fi
            if [ "$added_count" -gt 0 ]; then
                merged+=",\n"
            fi
            local healthy=false
            if ${SERVICE_CHECKERS[$key]}; then
                healthy=true
            fi
            merged+="  \"${key}_was_up\": $healthy"
            added_count=$((added_count + 1))
            _log "INFO" "Initialized state for new service \"$key\" (currently $healthy)"
        done
        merged+="\n}"
        # Write back the merged state so future runs don't re-initialize
        mkdir -p "$STATE_DIR"
        printf "$merged" > "$STATE_FILE"
        echo "$merged"
    else
        # First run: probe everything and record it as the initial state.
        local state="{"
        local first=true
        for key in "${!SERVICE_CHECKERS[@]}"; do
            $first || state+=", "
            first=false
            local healthy=false
            if ${SERVICE_CHECKERS[$key]}; then
                healthy=true
            fi
            state+="\"${key}_was_up\": $healthy"
        done
        state+="}"
        echo "$state"
    fi
}

# Save current state to JSON file.
_save_state() {
    mkdir -p "$STATE_DIR"
    local state="{\n"
    local first=true
    for key in "${!SERVICE_CHECKERS[@]}"; do
        $first || state+=",\n"
        first=false
        local was_up=false
        if ${SERVICE_CHECKERS[$key]}; then
            was_up=true
        fi
        state+="  \"${key}_was_up\": $was_up"
    done
    state+=",\n  \"last_checked\": \"$(date -Iseconds)\"\n}\n"
    printf "$state" > "$STATE_FILE"
}

# Restart a systemd user service.
_restart_service() {
    local svc="$1"
    local infra_name="$2"
    _log "INFO" "Restarting $svc ($infra_name recovered)..."
    if systemctl --user restart "$svc" 2>/dev/null; then
        _log "INFO" "$svc restarted successfully"
        return 0
    else
        _log "WARN" "Failed to restart $svc — may need manual intervention"
        return 1
    fi
}

# Parse a previous state value from JSON.
_get_prev_state() {
    local key="$1"
    local state="$2"
    echo "$state" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('${key}_was_up', False))
" 2>/dev/null || echo "false"
}

# ── Main ────────────────────────────────────────────────────────────────

main() {
    local state
    state=$(_load_state)

    local any_recovery=false

    for key in "${!SERVICE_CHECKERS[@]}"; do
        local name="${SERVICE_NAMES[$key]}"
        local check_fn="${SERVICE_CHECKERS[$key]}"

        # Get previous state
        local was_up
        was_up=$(_get_prev_state "${key}" "$state")

        # Check current health
        local is_up=false
        if $check_fn; then
            is_up=true
        fi

        # Detect transitions
        if [[ "$was_up" == "False" || "$was_up" == "false" ]] && [[ "$is_up" == "true" ]]; then
            _log "INFO" "$name recovered (was down, now up) — restarting dependent services"

            # Parse dependent services from semicolon-separated string
            IFS=';' read -ra deps <<< "${SERVICE_DEPENDENTS[$key]}"
            for svc in "${deps[@]}"; do
                _restart_service "$svc" "$name"
                sleep 1
            done

            _log "INFO" "All $name-dependent services restarted"
            any_recovery=true
        fi

        if [[ "$was_up" == "True" || "$was_up" == "true" ]] && [[ "$is_up" == "false" ]]; then
            _log "WARN" "$name went DOWN — dependent services may be affected"
        fi

        if [[ "$is_up" == "true" ]]; then
            _log "DEBUG" "$name is healthy"
        else
            _log "DEBUG" "$name is DOWN"
        fi
    done

    # Persist current state
    _save_state

    if $any_recovery; then
        _log "INFO" "Recovery actions completed"
    fi
}

main "$@"
