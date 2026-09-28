#!/usr/bin/env bash
# substance-service.sh — control substance.service (a systemd USER unit)
# from any shell, including agent shells that lack XDG_RUNTIME_DIR.
#
# WHY THIS EXISTS (incident 2026-09-27, records fd81b748 / 2d370d65):
# substance.service is a *user* unit (~/.config/systemd/user/substance.service,
# enabled for boot). Agent shells (Freebuff/harness/CRON) usually have no
# XDG_RUNTIME_DIR, so plain `systemctl --user` fails with "Failed to connect
# to bus" and operators fall back to manual `setsid uvicorn ...` restarts.
# That TERM-kills the unit's main PID; uvicorn exits cleanly, so
# Restart=on-failure correctly does not fire, and the unit is left
# `inactive (dead)` while an unmanaged process squats :3115. This script
# sets XDG_RUNTIME_DIR automatically and wraps systemctl --user, so restarts
# go through systemd and the unit never gets orphaned.
#
# Usage:
#   substance-service.sh                 # status (default)
#   substance-service.sh status          # systemctl --user status (long)
#   substance-service.sh start|stop|restart
#   substance-service.sh enable|disable [--now]   # enable --now works
#   substance-service.sh logs [N]        # journalctl --user -n N (default 50)
#   substance-service.sh health          # curl /healthz, exit 0 iff 200
#   substance-service.sh -h|--help
#
# Env overrides (used by tests/bin/tests/test_substance_service.py):
#   SUBSTANCE_UNIT        unit name          (default: substance.service)
#   SUBSTANCE_HEALTH_URL  health endpoint    (default: http://localhost:3115/healthz)
#   SYSTEMCTL             systemctl binary   (default: systemctl)
#   JOURNALCTL            journalctl binary  (default: journalctl)
#
# Exit codes: 0 ok, 1 operation error (incl. unhealthy), 2 usage error.
set -u

UNIT="${SUBSTANCE_UNIT:-substance.service}"
HEALTH_URL="${SUBSTANCE_HEALTH_URL:-http://localhost:3115/healthz}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
JOURNALCTL="${JOURNALCTL:-journalctl}"

USAGE="Usage: substance-service.sh [status|start|stop|restart|enable|disable|logs [N]|health|-h]
Controls substance.service (systemd USER unit) with XDG_RUNTIME_DIR set
automatically, so it works from agent shells. Default action: status.
Never restart substance with manual setsid/uvicorn — it orphans the unit."

# ── XDG_RUNTIME_DIR guard (the whole point of this script) ────────────
# The systemd user manager is reachable only through
# $XDG_RUNTIME_DIR/systemd/private. Agent shells frequently lack the var;
# default it the same way systemd's session setup does.
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    XDG_RUNTIME_DIR="/run/user/$(id -u)"
    export XDG_RUNTIME_DIR
fi

run_systemctl() {
    # Capture the exit status IMMEDIATELY: `rc=$?` after a failed `if`
    # would read the if-statement's own status (0), not the command's.
    "$SYSTEMCTL" --user "$@"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "ERROR: systemctl --user $* failed (rc=$rc)" >&2
        echo "       If the error is 'Failed to connect to bus', the user manager" >&2
        echo "       is unreachable (XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-unset}); a" >&2
        echo "       persistent-login session must own this process." >&2
        return "$rc"
    fi
}

ACTION="${1:-status}"

case "$ACTION" in
    -h|--help|help)
        echo "$USAGE"
        exit 0
        ;;
    status)
        run_systemctl status --no-pager -l "$UNIT"
        exit $?
        ;;
    start|stop|restart)
        run_systemctl "$ACTION" "$UNIT"
        exit $?
        ;;
    enable|disable)
        shift
        run_systemctl "$ACTION" "$UNIT" "$@"
        exit $?
        ;;
    logs)
        N="${2:-50}"
        exec "$JOURNALCTL" --user -u "$UNIT" -n "$N" --no-pager
        ;;
    health)
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL")
        rc=$?
        if [ "$rc" -ne 0 ]; then
            echo "ERROR: cannot reach $HEALTH_URL (curl rc=$rc)" >&2
            exit 1
        fi
        if [ "$code" = "200" ]; then
            echo "OK 200 $HEALTH_URL"
            exit 0
        fi
        echo "UNHEALTHY $code $HEALTH_URL" >&2
        exit 1
        ;;
    *)
        echo "ERROR: unknown action: $ACTION" >&2
        echo "$USAGE" >&2
        exit 2
        ;;
esac
