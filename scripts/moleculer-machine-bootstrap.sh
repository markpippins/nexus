#!/usr/bin/env bash
# moleculer-machine-bootstrap.sh — automate the devops quickstart on a fresh host.
#
# Companion to the quickstart posted to the Assembly devops forum (thread
# 53356e55) and the port inventory in discussions (692bce76). Ports and app
# names come from the SINGLE-SOURCE registry moleculer/ports.yaml — never
# duplicate them here.
#
# Design rules:
#   - explicit modes only; no daemons are left running by --check/--install/--test
#   - --start runs ONE app in the FOREGROUND (Ctrl-C to stop); use --dry-run
#     to print the command without executing it
#   - --unit PRINTS a systemd --user unit template to stdout; it never writes
#     or enables anything
#   - standalone mode (no NATS) is the default bring-up path for a play box;
#     pass --mesh for the mesh-joined variant
#
# Usage:
#   moleculer-machine-bootstrap.sh --check
#   moleculer-machine-bootstrap.sh --list
#   moleculer-machine-bootstrap.sh --install [apps...]        # default: all registry apps
#   moleculer-machine-bootstrap.sh --test <apps...>
#   moleculer-machine-bootstrap.sh --start <app> [--standalone|--mesh] [--dry-run]
#   moleculer-machine-bootstrap.sh --unit <app>
#   moleculer-machine-bootstrap.sh --drift
#
# Environment:
#   NEXUS_BOOTSTRAP_ROOT   repo root override (tests)
#   BOOTSTRAP_NATS_TARGET  host:port probed for NATS (default 127.0.0.1:4222)
#   BOOTSTRAP_PG_TARGET    host:port probed for PostgreSQL (default 127.0.0.1:5432)
#   BOOTSTRAP_MIN_NODE     minimum Node major version (default 20)
set -u

SELF="$(basename "$0")"
die()  { echo "FATAL: $*" >&2; exit 2; }
warn() { echo "WARN:  $*" >&2; }
info() { echo "       $*"; }

# ── resolve repo root ────────────────────────────────────────────────────────
if [ -n "${NEXUS_BOOTSTRAP_ROOT:-}" ]; then
    ROOT="$NEXUS_BOOTSTRAP_ROOT"
elif command -v git >/dev/null 2>&1; then
    ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
[ -n "${ROOT:-}" ] && [ -d "$ROOT/moleculer" ] || die "cannot locate repo root (set NEXUS_BOOTSTRAP_ROOT)"

PORTS_YAML="$ROOT/moleculer/ports.yaml"
[ -r "$PORTS_YAML" ] || die "registry not readable: $PORTS_YAML"

# ── registry parsing (ports.yaml is the single source) ──────────────────────
registry_apps() {
    # one "name:port" per line, infra (map_row-carrying) entries included
    awk '
        /^infra:/      { sec="infra";  next }
        /^canary:/     { sec="canary"; next }
        /^  - port:/   { p=$3; next }
        sec != "" && /^    name:/ {
            sub(/^    name: *"/, "", $0)
            sub(/".*$/, "", $0)
            print $0 ":" p
        }
    ' "$PORTS_YAML"
}

registry_port_of() {
    local want="$1" e
    for e in $(registry_apps); do
        [ "${e%%:*}" = "$want" ] && { echo "${e##*:}"; return 0; }
    done
    return 1
}

# ── prereq checks (--check) ─────────────────────────────────────────────────
node_major() { node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }
probe_tcp()  { (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null && exec 3>&- && return 0 || return 1; }

check_prereqs() {
    local rc=0 min="${BOOTSTRAP_MIN_NODE:-20}"
    echo "== prereq check (root: $ROOT) =="

    if command -v node >/dev/null 2>&1; then
        local major; major="$(node_major)"
        if [ "$major" -ge "$min" ] 2>/dev/null; then
            info "node: $(node -v) OK (>= $min)"
        else
            warn "node $(node -v) is older than the required major $min"; rc=1
        fi
    else
        warn "node not found on PATH"; rc=1
    fi

    command -v npm     >/dev/null 2>&1 && info "npm: $(npm -v)"     || { warn "npm not found"; rc=1; }
    command -v git     >/dev/null 2>&1 && info "git: present"        || { warn "git not found"; rc=1; }
    command -v python3 >/dev/null 2>&1 && info "python3: present"    || { warn "python3 not found (needed for the drift gate)"; rc=1; }

    if python3 -c 'import yaml' 2>/dev/null; then
        info "python3: pyyaml importable"
    else
        warn "pyyaml missing (pip install pyyaml) — the drift gate needs it"; rc=1
    fi

    # Soft checks: standalone mode needs NEITHER of these; mesh mode needs NATS;
    # any DB-backed twin needs PostgreSQL. Warnings only — never hard-fail.
    local nat="${BOOTSTRAP_NATS_TARGET:-127.0.0.1:4222}"
    local pg="${BOOTSTRAP_PG_TARGET:-127.0.0.1:5432}"
    if probe_tcp "${nat%%:*}" "${nat##*:}"; then
        info "NATS reachable at $nat (mesh mode available)"
    else
        warn "NATS not reachable at $nat — fine for --standalone, required for --mesh"
    fi
    if probe_tcp "${pg%%:*}" "${pg##*:}"; then
        info "PostgreSQL reachable at $pg"
    else
        warn "PostgreSQL not reachable at $pg — twins need it (or a scratch snapshot)"
    fi

    if [ "$rc" -eq 0 ]; then echo "CHECK: OK"; else echo "CHECK: FAILED (see warnings)"; fi
    return "$rc"
}

# ── per-app actions ──────────────────────────────────────────────────────────
app_dir() { echo "$ROOT/moleculer/$1"; }

require_app() {
    [ -d "$(app_dir "$1")" ] || die "no such moleculer app: $1 (see --list)"
}

npm_in() {
    local dir="$1"; shift
    ( cd "$dir" && "$@" )
}

do_install() {
    local apps="$1" rc=0 d
    for a in $apps; do
        require_app "$a"; d="$(app_dir "$a")"
        echo "== npm install: $a =="
        npm_in "$d" npm install || { warn "npm install failed for $a"; rc=1; }
    done
    return "$rc"
}

do_test() {
    local apps="$1" rc=0 d
    for a in $apps; do
        require_app "$a"; d="$(app_dir "$a")"
        echo "== npm test: $a =="
        npm_in "$d" npm test || { warn "npm test failed for $a"; rc=1; }
    done
    return "$rc"
}

# ── start (foreground; --dry-run prints without executing) ──────────────────
start_cmd() {  # prints the command for app $1 in mode $2 (standalone|mesh)
    local app="$1" mode="$2"
    if [ "$mode" = "standalone" ]; then
        echo "npx moleculer-runner --config ./moleculer.config.standalone.js 'services/**/*.service.ts'"
    else
        echo "npm start"
    fi
}

do_start() {
    local app="$1" mode="$2" dry="$3" port cmd
    require_app "$app"
    port="$(registry_port_of "$app")" || die "app $app not in the registry"
    [ -f "$(app_dir "$app")/moleculer.config.standalone.js" ] || [ "$mode" = "mesh" ] || \
        die "$app has no moleculer.config.standalone.js (use --mesh)"
    cmd="$(start_cmd "$app" "$mode")"
    echo "== start: $app (port $port, mode $mode) =="
    echo "   env: SERVICE_PORT=$port"
    if [ "$dry" = "1" ]; then
        echo "   cmd (dry-run): (cd $(app_dir "$app") && SERVICE_PORT=$port $cmd)"
        return 0
    fi
    info "Ctrl-C stops the app; the drift gate (\`$SELF --drift\`) is the contract check"
    ( cd "$(app_dir "$app")" && SERVICE_PORT="$port" eval "$cmd" )
}

# ── systemd unit template (stdout only; never written or enabled) ────────────
do_unit() {
    local app="$1" port
    require_app "$app"
    port="$(registry_port_of "$app")" || die "app $app not in the registry"
    cat <<EOF
# moleculer-$app.service — template printed by $SELF (--unit); install to
# ~/.config/systemd/user/ and edit paths/secrets. Never enabled by this script.
[Unit]
Description=moleculer $app (canary twin, port $port)
After=network-online.target

[Service]
WorkingDirectory=$ROOT/moleculer/$app
Environment=SERVICE_PORT=$port
Environment=NATS_URL=nats://127.0.0.1:4222
Environment=PG_HOST=127.0.0.1 PG_PORT=5432 PGDATABASE=nexus
ExecStart=$(command -v npm || echo /usr/bin/npm) start
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
}

# ── drift gate ───────────────────────────────────────────────────────────────
do_drift() {
    local rc=0
    echo "== port-registry byte-identity =="
    ( cd "$ROOT" && python3 tools/api-docs/gen_port_registry.py --check ) || rc=1
    echo "== contract drift (twin alias maps vs incumbent openapi.yaml) =="
    ( cd "$ROOT" && python3 tools/api-docs/check_drift.py ) || rc=1
    return "$rc"
}

# ── usage / arg parsing ──────────────────────────────────────────────────────
usage() {
    cat >&2 <<EOF
usage: $SELF <mode> [args]
  --check                        verify prereqs (node>=${BOOTSTRAP_MIN_NODE:-20} npm git python3/pyyaml; NATS/PG soft-warned)
  --list                         list apps and ports from moleculer/ports.yaml
  --install [apps...]            npm install (default: every registry app)
  --test <apps...>               npm test per app
  --start <app> [--standalone|--mesh] [--dry-run]
                                 start one app in the foreground (standalone = no NATS)
  --unit <app>                   print a systemd --user unit template to stdout
  --drift                        run the port-registry + contract drift gates
env: NEXUS_BOOTSTRAP_ROOT, BOOTSTRAP_NATS_TARGET, BOOTSTRAP_PG_TARGET, BOOTSTRAP_MIN_NODE
EOF
}

MODE="${1:-}"
[ -n "$MODE" ] || { usage; exit 1; }
shift

case "$MODE" in
    --check) check_prereqs ;;
    --list)
        printf '%-16s %s\n' "APP" "PORT"
        for e in $(registry_apps); do printf '%-16s %s\n' "${e%%:*}" "${e##*:}"; done
        ;;
    --install)
        if [ "$#" -gt 0 ]; then APPS="$*"; else APPS="$(registry_apps | sed 's/:.*//' | tr '\n' ' ')"; fi
        do_install "$APPS"
        ;;
    --test)
        [ "$#" -gt 0 ] || { usage; exit 1; }
        do_test "$*"
        ;;
    --start)
        APP=""; MODE2="standalone"; DRY=0
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --standalone) MODE2="standalone" ;;
                --mesh)       MODE2="mesh" ;;
                --dry-run)    DRY=1 ;;
                -*) usage; exit 1 ;;
                *)  APP="$1" ;;
            esac
            shift
        done
        [ -n "$APP" ] || { usage; exit 1; }
        do_start "$APP" "$MODE2" "$DRY"
        ;;
    --unit)
        [ "$#" -eq 1 ] || { usage; exit 1; }
        do_unit "$1"
        ;;
    --drift) do_drift ;;
    -h|--help|help) usage ;;
    *) usage; exit 1 ;;
esac
