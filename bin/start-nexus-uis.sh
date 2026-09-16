#!/bin/bash
# bin/start-nexus-uis.sh — manage ALL Nexus UI dev servers via systemd user units
#
# All 24 UIs run as systemd --user services, each in its own unit.
# This script provides a unified interface to start/stop/status them.
#
# Usage:
#   bin/start-nexus-uis.sh start    # start all nexus UIs (idempotent)
#   bin/start-nexus-uis.sh status   # show systemd status for all UIs
#   bin/start-nexus-uis.sh stop     # stop all nexus UIs
#   bin/start-nexus-uis.sh restart  # restart all nexus UIs
#   bin/start-nexus-uis.sh logs     # tail logs for a specific UI
#
# Previously used tmux (session "nexus-uis"). Migrated to systemd 2026-07-24.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXUS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── UI list (name, port) — sorted by port for status output ────────────
declare -A UI_PORTS

UI_NAMES=(
    "nebula-ui"          # 4210
    "duality-ui"         # 3002
    "view-architect"     # 3003
    "plurality-ui"       # 3004
    "nexus-console"      # 4200
    "conduit-ui"         # 4201 (live; mock was :3000, unbound in live mode)
    "tackle-ui"          # 4202
    "cascade-ui"         # 4203
    "execution-ui"       # 4205
    "peb-ui"             # 4206
    "semantic-kernel-ui" # 4207
    "vision-ui"          # 4208
    "wind-ui"            # 4209
    "throttler-ui"       # 4211
    "nebula-control-plane" # 4014 (live; mock was :3000)
    "monaco-judge"        # 4016
    "conduit-ui-legacy"  # 4015
    "data-explorer-ui"     # 4212
    "semantics-ui"         # 4213 (live; mock was :3000)
    "assembly-ui"          # 4214 (live; mock was :3000)
    "mildred-ui"           # 4215 (live; mock was :3000)
    "atlas-ui"             # 4216 (live; mock was :3000)
    "shrapnel-ui"          # 4217 (live; mock was :3000)
    "application-host"     # 4250 (standalone repo nexus-application-host)
)

UI_PORTS[nebula-ui]=4210
UI_PORTS[duality-ui]=3002
UI_PORTS[view-architect]=3003
UI_PORTS[plurality-ui]=3004
UI_PORTS[nexus-console]=4200
UI_PORTS[conduit-ui]=4201  # live mode (mock was :3000, intentionally unbound in live mode)
UI_PORTS[tackle-ui]=4202
UI_PORTS[cascade-ui]=4203
UI_PORTS[execution-ui]=4205
UI_PORTS[peb-ui]=4206
UI_PORTS[semantic-kernel-ui]=4207
UI_PORTS[vision-ui]=4208
UI_PORTS[wind-ui]=4209
UI_PORTS[throttler-ui]=4211
UI_PORTS[nebula-control-plane]=4014  # live mode (mock was :3000, intentionally unbound in live mode)
UI_PORTS[monaco-judge]=4016
UI_PORTS[conduit-ui-legacy]=4015
UI_PORTS[data-explorer-ui]=4212
UI_PORTS[semantics-ui]=4213  # live mode (mock was :3000, intentionally unbound in live mode)
UI_PORTS[assembly-ui]=4214   # live mode (mock was :3000, intentionally unbound in live mode)
UI_PORTS[mildred-ui]=4215  # live mode (mock was :3000, intentionally unbound in live mode)
UI_PORTS[atlas-ui]=4216  # live mode (mock was :3000)
UI_PORTS[shrapnel-ui]=4217  # live mode (mock was :3000)
UI_PORTS[application-host]=4250  # standalone repo nexus-application-host (application-host.service)

# ── Helpers ─────────────────────────────────────────────────────────────

port_is_listening() {
    ss -tln 2>/dev/null | awk -v p=":$1$" '$4 ~ p {found=1} END {exit !found}'
}

# ── Commands ────────────────────────────────────────────────────────────

cmd_start_all() {
    echo "=== Starting Nexus UIs (via systemd) ==="

    # ── Standing operator lease (enforce-flip prerequisite, 65fe85a8) ──────
    # UI-origin /chat calls need an ACTIVE operator@ui-fleet lease when the
    # lease check flips to enforce. Non-fatal: fleet start must never fail
    # because lease infrastructure is down (script exits 0 + warns; the
    # operator-lease-renew.timer retries every 30 min).
    if command -v python3 >/dev/null 2>&1; then
        echo "  operator standing lease: $(python3 "$NEXUS_ROOT/bin/operator-lease.py" ensure 2>/dev/null || echo 'degraded (will retry via timer)')"
    fi

    systemctl --user daemon-reload 2>/dev/null || true

    echo
    for name in "${UI_NAMES[@]}"; do
        echo -n "  $name (port ${UI_PORTS[$name]}): "
        systemctl --user enable "$name.service" 2>/dev/null && \
          systemctl --user start "$name.service" 2>/dev/null && \
          echo "✅ started" || echo "❌ FAILED"
    done

    echo
    echo "Waiting for servers to bind..."
    sleep 10

    echo
    local all_up=true
    for name in "${UI_NAMES[@]}"; do
        local port="${UI_PORTS[$name]}"
        if port_is_listening "$port"; then
            echo "  ✅ $name — http://localhost:$port"
        else
            echo "  ⏳ $name — port $port not yet listening (may still be compiling)"
            all_up=false
        fi
    done

    echo
    if $all_up; then
        echo "All UIs are listening!"
    else
        echo "Some UIs are still compiling. Check with 'bin/start-nexus-uis.sh status'."
    fi
    echo "Check logs with: bin/start-nexus-uis.sh logs <name>"
    echo "=== Done ==="
}

cmd_status_all() {
    echo "=== Nexus UI Status (systemd) ==="
    printf "%-22s %-6s %-12s %-10s %s\n" "UI" "PORT" "SYSTEMD" "LISTENING" "URL"
    printf "%-22s %-6s %-12s %-10s %s\n" "--------------------" "------" "----------" "----------" "-------------------------"

    for name in "${UI_NAMES[@]}"; do
        local port="${UI_PORTS[$name]}"
        local active="inactive"
        local systemd_status
        systemd_status="$(systemctl --user is-active "$name.service" 2>/dev/null || echo "unknown")"
        case "$systemd_status" in
            active)   active="✅ active" ;;
            activating) active="⏳ compiling" ;;
            failed)   active="❌ failed" ;;
            inactive) active="💤 stopped" ;;
            *)        active="❓ $systemd_status" ;;
        esac
        local listening="❌"
        if port_is_listening "$port"; then
            listening="✅ UP"
        fi
        printf "%-22s %-6s %-12s %-10s http://localhost:%s\n" "$name" "$port" "$active" "$listening" "$port"
    done
}

cmd_stop_all() {
    echo "=== Stopping Nexus UIs (via systemd) ==="

    for name in "${UI_NAMES[@]}"; do
        echo -n "  $name: "
        systemctl --user stop "$name.service" 2>/dev/null && echo "stopped" || echo "already stopped"
    done

    # Verify
    echo
    local any_live=false
    for name in "${UI_NAMES[@]}"; do
        local port="${UI_PORTS[$name]}"
        if port_is_listening "$port"; then
            echo "  ⚠️  $name still on port $port — killing PID $(lsof -ti :$port 2>/dev/null)"
            kill "$(lsof -ti :"$port" 2>/dev/null)" 2>/dev/null || true
            any_live=true
        fi
    done

    if ! $any_live; then
        echo "All UIs stopped."
    fi
    echo "=== Done ==="
}

cmd_restart_all() {
    cmd_stop_all
    sleep 2
    cmd_start_all
}

cmd_logs() {
    local name="$1"
    if [ -z "$name" ]; then
        echo "Usage: bin/start-nexus-uis.sh logs <ui-name>"
        echo "Available: ${UI_NAMES[*]}"
        exit 1
    fi
    journalctl --user -u "$name.service" --no-pager -n 50 -f
}

# ── Main ────────────────────────────────────────────────────────────────

case "${1:-status}" in
    start)   cmd_start_all ;;
    status)  cmd_status_all ;;
    stop)    cmd_stop_all ;;
    restart) cmd_restart_all ;;
    logs)    cmd_logs "$2" ;;
    attach)
        echo "tmux session no longer used — UIs are managed by systemd."
        echo "Use 'bin/start-nexus-uis.sh logs <ui-name>' to view logs,"
        echo "or 'journalctl --user -u <ui-name>.service -f' to follow."
        ;;
    *)
        echo "Usage: $0 {start|status|stop|restart|logs|attach}"
        exit 1
        ;;
esac