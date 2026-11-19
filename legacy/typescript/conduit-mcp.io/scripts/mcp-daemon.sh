#!/usr/bin/env bash
# ── MCP Server Daemon ────────────────────────────────────────────────
# Auto-restarts the conduit-mcp server on crash.  Detects hung
# processes (health check fails for 30s) and kills + restarts.
#
# Usage:
#   ./scripts/mcp-daemon.sh start
#   ./scripts/mcp-daemon.sh stop
#   ./scripts/mcp-daemon.sh restart
#   ./scripts/mcp-daemon.sh status
#
# Configurable via environment:
#   MCP_PORT          — port to listen on (default 3100)
#   MCP_LOG           — log file (default /tmp/mcp-daemon.log)
#   MCP_PID_FILE      — pid file (default /tmp/mcp-daemon.pid)
#   MCP_RESTART_DELAY — seconds between restart attempts (default 3)
#   MCP_HEALTH_TTL    — seconds of failed health checks before kill (default 30)
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export MCP_PORT="${MCP_PORT:-3100}"
export MCP_LOG="${MCP_LOG:-/tmp/mcp-daemon.log}"
export MCP_PID_FILE="${MCP_PID_FILE:-/tmp/mcp-daemon.pid}"
export MCP_RESTART_DELAY="${MCP_RESTART_DELAY:-3}"
export MCP_HEALTH_TTL="${MCP_HEALTH_TTL:-30}"

# ── helpers ──────────────────────────────────────────────────────────

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] mcp-daemon $*" | tee -a "$MCP_LOG"; }

daemon_running() {
  local pid
  pid="$(cat "$MCP_PID_FILE" 2>/dev/null || true)"
  if [ -z "$pid" ]; then return 1; fi
  kill -0 "$pid" 2>/dev/null
}

mcp_reachable() {
  curl -sf "http://localhost:${MCP_PORT}/health" >/dev/null 2>&1
}

# ── watchdog ── runs as a background process, auto-restarts MCP ──────

run_watchdog() {
  local restart_count=0
  local health_interval=5
  local max_fails=$(( MCP_HEALTH_TTL / health_interval ))
  local mcp_pid=""

  # Trap: when the watchdog receives TERM/INT, cascade-kill the MCP child
  trap 'log "Watchdog shutting down..."; kill $mcp_pid 2>/dev/null || true; exit 0' TERM INT

  while true; do
    log "Launching MCP server (restart #$restart_count)..."
    cd "$PROJECT_DIR"

    npx tsx src/index.ts >> "$MCP_LOG" 2>&1 &
    mcp_pid=$!
    log "MCP server started (PID $mcp_pid)"

    local consecutive_health_fails=0
    while kill -0 $mcp_pid 2>/dev/null; do
      sleep "$health_interval"

      if curl -sf "http://localhost:${MCP_PORT}/health" >/dev/null 2>&1; then
        consecutive_health_fails=0
      else
        consecutive_health_fails=$((consecutive_health_fails + 1))
        if [ $consecutive_health_fails -ge $max_fails ]; then
          log "Health check failed for ${MCP_HEALTH_TTL}s — killing hung MCP (PID $mcp_pid)"
          kill -9 $mcp_pid 2>/dev/null || true
          break
        fi
      fi
    done

    wait $mcp_pid 2>/dev/null || true
    local exit_code=$?
    log "MCP server exited (code $exit_code). Restarting in ${MCP_RESTART_DELAY}s..."
    restart_count=$((restart_count + 1))
    sleep "$MCP_RESTART_DELAY"
  done
}

# ── commands ─────────────────────────────────────────────────────────

cmd_start() {
  if daemon_running; then
    log "MCP daemon already running (PID $(cat "$MCP_PID_FILE"))"
    echo "Daemon already running."
    exit 0
  fi

  # Clean up any orphaned MCP processes from previous runs
  pkill -f "tsx.*src/index.ts" 2>/dev/null || true
  sleep 1

  log "Starting MCP daemon (project=$PROJECT_DIR, port=$MCP_PORT)"
  log "Log: $MCP_LOG  |  Pid: $MCP_PID_FILE"

  # Launch watchdog as a regular background function — no setsid
  run_watchdog >> "$MCP_LOG" 2>&1 &
  local daemon_pid=$!
  echo "$daemon_pid" > "$MCP_PID_FILE"
  log "Daemon watchdog started (PID $daemon_pid)"

  # Wait a moment and verify it's alive
  sleep 3
  if daemon_running; then
    log "Daemon is alive."
    echo "Daemon started (PID $daemon_pid)"
  else
    log "ERROR: Daemon failed to start. Check $MCP_LOG"
    echo "Daemon failed to start. Check $MCP_LOG"
    exit 1
  fi
}

cmd_stop() {
  if ! daemon_running; then
    log "MCP daemon not running"
    rm -f "$MCP_PID_FILE"
    # Clean up orphaned MCP anyway
    pkill -f "tsx.*src/index.ts" 2>/dev/null || true
    echo "Daemon not running."
    exit 0
  fi

  local daemon_pid
  daemon_pid="$(cat "$MCP_PID_FILE")"
  log "Stopping MCP daemon (PID $daemon_pid)..."

  # Send TERM — watchdog trap will cascade-kill MCP child
  kill -TERM "$daemon_pid" 2>/dev/null || true
  sleep 2

  # Force kill if still alive
  if kill -0 "$daemon_pid" 2>/dev/null; then
    kill -9 "$daemon_pid" 2>/dev/null || true
  fi

  # Fallback to ensure no MCP orphans remain
  pkill -f "tsx.*src/index.ts" 2>/dev/null || true

  rm -f "$MCP_PID_FILE"
  log "MCP daemon stopped"
  echo "Daemon stopped."
}

cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start
}

cmd_status() {
  if daemon_running; then
    local pid
    pid="$(cat "$MCP_PID_FILE")"
    echo "MCP daemon: RUNNING (PID $pid)"
    if mcp_reachable; then
      echo "Health:     OK (port $MCP_PORT)"
    else
      echo "Health:     UNREACHABLE (port $MCP_PORT)"
    fi
  else
    echo "MCP daemon: STOPPED"
    rm -f "$MCP_PID_FILE"
  fi
}

# ── main ─────────────────────────────────────────────────────────────

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
