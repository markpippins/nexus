#!/usr/bin/env bash
# bin/mysql-health-monitor.sh — MySQL (Kodi shared library) health watcher
# =========================================================================
#
# Monitors my-mysql container liveness and backup freshness. Designed to be
# run via the mysql-health-monitor.timer (every 5 min) or standalone.
#
# What it does (per cycle):
#   1. Liveness: docker healthcheck status (primary), TCP :3306 fallback.
#   2. UP  -> DOWN transition: WARN log + nebula incident (to:sysadmin,
#      source:mysql-health). Docker `restart: unless-stopped` handles
#      container restarts, so we only ALERT, we do not restart anything.
#   3. DOWN -> UP transition: INFO log + nebula resolved record (closes the
#      incident loop for the sysadmin inbox).
#   4. Backup freshness: reads $BACKUP_DIR/last-backup.json stamped by
#      mysql-backup.sh. If the last successful backup is older than
#      STALE_HOURS, posts an incident (source:mysql-backup) — once per stale
#      episode (state flag), re-arms when a fresh backup appears.
#
# Exit codes:
#   0 — normal cycle
#   1 — state file error

set -uo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}/nexus-monitor"
STATE_FILE="$STATE_DIR/mysql-health-state.json"
BACKUP_DIR="${BACKUP_DIR:-/home/codex/backups/mysql}"
BACKUP_STAMP="$BACKUP_DIR/last-backup.json"
STALE_HOURS="${STALE_HOURS:-28}"          # daily backup at ~03:45; 28h headroom
CONTAINER="${CONTAINER:-my-mysql}"
NEBULA_URL="${NEBULA_URL:-http://localhost:3101/api/agent-records}"

# Drive guard (audit f74eb976): name the actual backup-dir state in alerts
# (dangling symlink -> absent removable drive vs. genuinely stale backups).
# shellcheck source=bin/lib/drive-guard.sh
. "$BIN_DIR/lib/drive-guard.sh" \
  || { echo "drive-guard: FATAL: lib load failed" >&2; exit 1; }

_log() {
  local level="$1"; shift
  echo "[mysql-health-monitor] $(date '+%Y-%m-%d %H:%M:%S') [$level] $*"
}

incident() {  # best-effort alert; never blocks the monitor
  local title="$1" source_tag="$2" status="$3" detail="$4"
  curl -s --max-time 5 -X POST "$NEBULA_URL" \
    -H 'Content-Type: application/json' \
    -d "{\"recordType\":\"report\",\"role\":\"devops\",\"title\":\"$title\",\"content\":\"$detail\",\"tags\":[\"to:sysadmin\",\"type:incident\",\"status:$status\",\"source:$source_tag\"]}" \
    >/dev/null 2>&1 || true
}

# ------------------------------------------------------------- liveness ---
# Primary: docker healthcheck (compose defines mysqladmin ping).
# Fallback: container running + TCP 3306 reachable.
_mysql_healthy() {
  if command -v docker &>/dev/null; then
    local state
    state=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null)
    if [[ -n "$state" && "$state" != "starting" && "$state" != "none" ]]; then
      [[ "$state" == "healthy" ]] && return 0 || return 1
    fi
    # container missing entirely -> docker inspect printed nothing
    if ! docker inspect "$CONTAINER" &>/dev/null; then
      : # fall through to port check (container may be mid-recreation)
    else
      # container exists, health not (yet) reported — port check decides
      docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER" || return 1
    fi
  fi
  ss -tlnp 2>/dev/null | grep -q ':3306 ' 2>/dev/null && return 0
  timeout 2 bash -c 'echo > /dev/tcp/localhost/3306' 2>/dev/null && return 0
  return 1
}

# -------------------------------------------------------------- state -----
_load_state() {
  if [[ -f "$STATE_FILE" ]]; then
    cat "$STATE_FILE" 2>/dev/null || echo '{"mysql_was_up":false,"stale_alerted":false}'
  else
    # first run: initialize to reality so we never fabricate a transition
    local up=false
    _mysql_healthy && up=true
    echo "{\"mysql_was_up\":$up,\"stale_alerted\":false}"
  fi
}

_save_state() {
  local was_up="$1" stale_alerted="$2"
  mkdir -p "$STATE_DIR"
  cat > "$STATE_FILE" <<EOF
{
  "mysql_was_up": $was_up,
  "stale_alerted": $stale_alerted,
  "last_checked": "$(date -Iseconds)"
}
EOF
}

_backup_age_hours() {
  # missing/invalid stamp -> treat as extremely stale so we ALWAYS alert
  [[ -f "$BACKUP_STAMP" ]] || { echo 999999; return; }
  local last
  last=$(python3 -c "import sys,json;print(json.load(open('$BACKUP_STAMP')).get('last_backup',''))" 2>/dev/null) || last=""
  [[ -n "$last" ]] || { echo 999999; return; }
  python3 - "$last" <<'PY' 2>/dev/null || echo 999999
import sys, datetime
try:
    t = datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
    now = datetime.datetime.now(t.tzinfo)
    print(max(0, int((now - t).total_seconds() // 3600)))
except Exception:
    print(999999)
PY
}

# ---------------------------------------------------------------- main ----
main() {
  local state; state=$(_load_state)
  local mysql_was_up stale_alerted
  mysql_was_up=$(echo "$state" | python3 -c "import sys,json;d=json.load(sys.stdin);print(str(d.get('mysql_was_up',False)).lower())" 2>/dev/null || echo "false")
  stale_alerted=$(echo "$state" | python3 -c "import sys,json;d=json.load(sys.stdin);print(str(d.get('stale_alerted',False)).lower())" 2>/dev/null || echo "false")

  local mysql_is_up=false
  _mysql_healthy && mysql_is_up=true

  # ── UP -> DOWN: alert ──
  if [[ "$mysql_was_up" == "true" ]] && [[ "$mysql_is_up" != "true" ]]; then
    _log "WARN" "MySQL went DOWN — Kodi shared library unavailable"
    incident "mysql-health-monitor: my-mysql DOWN" "mysql-health" open \
      "my-mysql (MySQL 8.0, Kodi shared library) went DOWN at $(date -Iseconds). Container auto-restarts via docker compose (restart: unless-stopped). Check: docker compose -f nexus/docker/mysql/docker-compose.yml ps; journalctl --user -u mysql-health-monitor.service"
  fi

  # ── DOWN→UP: recovery ──
  if [[ "$mysql_was_up" != "true" ]] && [[ "$mysql_is_up" == "true" ]]; then
    _log "INFO" "MySQL recovered (was down, now up)"
    incident "mysql-health-monitor: my-mysql recovered" "mysql-health" "resolved" \
      "MySQL is back UP at $(date +%s)."
  fi

  # ── Backup freshness (success/failure alert side) ──
  local age
  age=$(_backup_age_hours)
  local drive_state
  drive_state="$(drive_guard_describe "$BACKUP_DIR")"
  if [[ "$age" -gt "$STALE_HOURS" ]] && [[ "$stale_alerted" != "true" ]]; then
    _log "WARN" "No successful MySQL backup in ${age}h (> ${STALE_HOURS}h) [backup dir: $drive_state]"
    incident "mysql-backup: STALE (no success in ${age}h)" "mysql-backup" "open" \
      "No successful MySQL backup stamp in ${age}h (threshold ${STALE_HOURS}h). Backup dir state: $drive_state. Last stamp: $(cat "$BACKUP_STAMP" 2>/dev/null | tr -d '\n' || echo none). Check mysql-backup.service / $BACKUP_DIR/mysql-backup.log."
    stale_alerted=true
  elif [[ "$age" -le "$STALE_HOURS" ]]; then
    # fresh success present — re-arm the stale flag for the next episode
    if [[ "$stale_alerted" == "true" ]]; then
      _log "INFO" "MySQL backup fresh again (${age}h) — stale alert cleared"
      incident "mysql-backup: fresh again" "mysql-backup" "resolved" \
        "MySQL backup succeeded at $(date '+%H:%M') — staleness incident resolved (age ${age}h)."
    fi
    stale_alerted=false
  fi

  if [[ "$mysql_is_up" == "true" ]]; then
    _log "DEBUG" "MySQL healthy (backup age ${age}h, backup dir: $drive_state)"
  else
    _log "DEBUG" "MySQL is DOWN"
  fi

  _save_state "$mysql_is_up" "$stale_alerted"
}

main "$@"