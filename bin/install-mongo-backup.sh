#!/usr/bin/env bash
# bin/install-mongo-backup.sh — deploy the MongoDB backup script + systemd units.
# =====================================================================
#
# Why this exists: the unit cannot point at `bin/mongo-backup.sh` inside a git
# checkout that may or may not have the file. Before this PR merged, the unit
# pointed at /home/codex/dev/nexus/bin/mongo-backup.sh — a path that only exists
# on the branch — so `systemctl --user start mongo-backup.service` failed with
# "Command ... is not executable: No such file or directory" and would have
# failed the same way at 04:05 every night.
#
# The unit therefore runs a HOST-STABLE installed copy, and this script is the
# one step that puts it there:
#
#     bin/install-mongo-backup.sh                # install + daemon-reload
#     bin/install-mongo-backup.sh --enable       # ... then arm the timer
#
# Installed layout (self-contained, so the script's own `$BIN_DIR/lib/...`
# resolution keeps working):
#
#     ~/.local/lib/nexus/mongo-backup/mongo-backup.sh
#     ~/.local/lib/nexus/mongo-backup/lib/drive-guard.sh
#     ~/.config/systemd/user/mongo-backup.{service,timer}
#
# Usage:
#   install-mongo-backup.sh [--enable] [--no-reload] [--dry-run]
#
#   --enable      also `systemctl --user enable --now mongo-backup.timer`
#   --no-reload   skip `systemctl --user daemon-reload` (tests, offline hosts)
#   --dry-run     print what would be installed, touch nothing
#
# Exit codes: 0 ok · 1 failure · 3 bad usage

set -euo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$BIN_DIR/.." && pwd)"
DEST="${MONGO_BACKUP_LIBDIR:-$HOME/.local/lib/nexus/mongo-backup}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

ENABLE=0
RELOAD=1
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --enable)    ENABLE=1 ;;
        --no-reload) RELOAD=0 ;;
        --dry-run)   DRY_RUN=1 ;;
        -h|--help)   sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install-mongo-backup: unknown option: $arg" >&2; exit 3 ;;
    esac
done

say() { echo "[install-mongo-backup] $*"; }

install_one() {  # <src> <dest> <mode>
    local src="$1" dest="$2" mode="$3"
    if [ "$DRY_RUN" = 1 ]; then
        say "would install $src -> $dest (mode $mode)"
        return 0
    fi
    install -m "$mode" "$src" "$dest"
    say "installed $dest (mode $mode, $(stat -c %s "$dest") bytes)"
}

say "repo:     $REPO_ROOT"
say "libdest:  $DEST"
say "unitdir:  $UNIT_DIR"
[ "$DRY_RUN" = 1 ] || { install -d "$DEST/lib" "$UNIT_DIR"; }

# Script + the lib it sources. Both are required: mongo-backup.sh resolves
# drive-guard.sh from its own directory, so a lone script copy fails at start.
install_one "$BIN_DIR/mongo-backup.sh"      "$DEST/mongo-backup.sh"      0755
install_one "$BIN_DIR/lib/drive-guard.sh"   "$DEST/lib/drive-guard.sh"   0644

# Units. ExecStart in the service is %h/.local/lib/nexus/mongo-backup/mongo-backup.sh,
# which is exactly the path populated above.
install_one "$REPO_ROOT/config/systemd/mongo-backup.service" "$UNIT_DIR/mongo-backup.service" 0644
install_one "$REPO_ROOT/config/systemd/mongo-backup.timer"   "$UNIT_DIR/mongo-backup.timer"   0644

if [ "$DRY_RUN" = 1 ]; then
    say "dry run: nothing written, no daemon-reload, no timer change"
    exit 0
fi

if [ "$RELOAD" = 1 ]; then
    if systemctl --user daemon-reload 2>/dev/null; then
        say "systemctl --user daemon-reload ok"
    else
        say "WARNING: daemon-reload failed (no user systemd session?) — units are in place, reload later"
    fi
fi

if [ "$ENABLE" = 1 ]; then
    systemctl --user enable --now mongo-backup.timer
    say "timer armed:"
    systemctl --user list-timers mongo-backup.timer --no-pager | sed 's/^/    /'
else
    say "next: systemctl --user enable --now mongo-backup.timer"
fi

say "done"
