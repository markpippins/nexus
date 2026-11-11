#!/bin/bash
# start-mongodb-docker.sh — start MongoDB via Docker with cleanup
# Called by systemd unit: mongodb.service
#
# ── Version safety (architect handoff c9540d99 §3.3) ───────────────────
# This script used to try mongo:4.4.18, then 4.2.18, then 4.0.28, taking the
# first image that started. After the data volume is upgraded — which the
# 4.4 -> 8.0 migration does, four times — the first entry is OLDER than the
# data files. mongod then refuses to open the volume, the loop falls through
# to even older images for the same reason, and the script reports "Failed to
# start MongoDB container with all version attempts." The §3.3 hazard is that
# someone then "fixes" it by removing the volume, which is data loss.
#
# The ladder is gone. There is exactly one image, and a recorded volume
# version that refuses any image older than the data it would open:
#
#   MONGO_VERSION              image to run (default mongo:4.4.18)
#   MONGO_VOLUME_VERSION_FILE  where the volume's version is recorded
#                              (default ~/.local/state/nexus/mongodb-volume-version)
#   MONGO_ALLOW_OLDER_IMAGE=1  escape hatch — documented, loud, and refused
#                              unless the version file is genuinely wrong
#
# The guard runs BEFORE the running container is stopped or removed, so a
# refused start changes nothing at all.

set -e

CONTAINER_NAME="atomic-mongodb"
MONGO_PORT="27017"
MONGO_VERSION="${MONGO_VERSION:-mongo:4.4.18}"
VOLUME_VERSION_FILE="${MONGO_VOLUME_VERSION_FILE:-$HOME/.local/state/nexus/mongodb-volume-version}"

# ── Credentials + bind (MongoDB integration pass, To Do 7b0d775d) ──────
# Creds are no longer literals in this script. They come from the repo
# .env (gitignored) when present, and only fall back to the historical
# dev defaults so an existing volume (initialized with those creds) keeps
# working. Set MONGO_ROOT_USER / MONGO_ROOT_PASS in nexus/.env and rotate
# via a fresh volume when ready — see docs/t25/0.1-hardcoded-port-cred-inventory.md.
NEXUS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$NEXUS_ROOT/.env" ]; then
    # shellcheck disable=SC1091
    set -a; . "$NEXUS_ROOT/.env"; set +a
fi
MONGO_ROOT_USER="${MONGO_ROOT_USER:-mongoUser}"
MONGO_ROOT_PASS="${MONGO_ROOT_PASS:-somePassword}"

# Bind address for the published port. Default preserves the historical
# 0.0.0.0 exposure for compatibility; set MONGO_BIND_ADDR to narrow it.
# NOTE: a plain 127.0.0.1 bind BREAKS the containerized consumers
# (cand-broker and the vanadium tier reach mongod via
# host.docker.internal, which resolves to the docker bridge gateway, not
# loopback). To drop LAN exposure without breaking them, bind the bridge:
#   MONGO_BIND_ADDR=172.17.0.1
MONGO_BIND_ADDR="${MONGO_BIND_ADDR:-0.0.0.0}"

# NOTE: this script previously ran `docker system prune -f` on every start,
# which deletes dangling images / stopped containers / unused networks
# system-wide. That is a destructive side effect unrelated to starting
# mongod, and it can remove artifacts other stacks still need. Removed.

# ── Version guard ───────────────────────────────────────────────────────
# "mongo:4.4.18" -> "4.4.18". A floating tag (e.g. "mongo:latest") has no
# comparable version, so it is treated as unknown and only warned about.
image_version() {
    local ref="$1"
    printf '%s' "${ref##*:}"
}

version_ge() {  # $1 >= $2 ?
    [ "$1" = "$2" ] && return 0
    [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$2" ]
}

REQUESTED_VERSION="$(image_version "$MONGO_VERSION")"
DATA_VERSION=""
if [ -f "$VOLUME_VERSION_FILE" ]; then
    DATA_VERSION="$(tr -d '[:space:]' < "$VOLUME_VERSION_FILE" 2>/dev/null || true)"
fi

if [ -n "$DATA_VERSION" ] && printf '%s' "$REQUESTED_VERSION" | grep -qE '^[0-9]'; then
    if ! version_ge "$REQUESTED_VERSION" "$DATA_VERSION"; then
        if [ "${MONGO_ALLOW_OLDER_IMAGE:-0}" = "1" ]; then
            echo "[mongodb] WARNING: starting $MONGO_VERSION is OLDER than the data volume ($DATA_VERSION)." >&2
            echo "[mongodb] WARNING: MONGO_ALLOW_OLDER_IMAGE=1 was set explicitly; this usually means mongod will refuse the volume." >&2
        else
            echo "[mongodb] REFUSING to start an image older than the data volume." >&2
            echo "[mongodb]   requested image : $MONGO_VERSION  (version $REQUESTED_VERSION)" >&2
            echo "[mongodb]   data volume     : mongodb_data remembers $DATA_VERSION ($VOLUME_VERSION_FILE)" >&2
            echo "[mongodb] A newer mongod upgraded this volume; older binaries cannot open it." >&2
            echo "[mongodb] Fix: start a matching-or-newer image, e.g." >&2
            echo "[mongodb]       MONGO_VERSION=mongo:${DATA_VERSION%%%.*}.${DATA_VERSION#*.} bin/start-mongodb-docker.sh" >&2
            echo "[mongodb] Do NOT delete or re-initialise the volume to work around this: that is data loss," >&2
            echo "[mongodb] not a repair (architect handoff c9540d99 §3.3)." >&2
            exit 2
        fi
    fi
elif [ -n "$DATA_VERSION" ]; then
    echo "[mongodb] WARNING: $MONGO_VERSION has no comparable version tag; cannot verify it is not older than $DATA_VERSION." >&2
fi

# Stop and remove existing container if it exists
if [ "$(docker ps -q -f name=${CONTAINER_NAME})" ]; then
    echo "[mongodb] Stopping existing container..."
    docker stop ${CONTAINER_NAME} 2>/dev/null || true
fi

if [ "$(docker ps -a -q -f name=${CONTAINER_NAME})" ]; then
    echo "[mongodb] Removing existing container..."
    docker rm ${CONTAINER_NAME} 2>/dev/null || true
fi

echo "[mongodb] Starting MongoDB container ($MONGO_VERSION)..."
if ! docker run -d --name ${CONTAINER_NAME} -p "${MONGO_BIND_ADDR}:${MONGO_PORT}:27017" \
    -e MONGO_INITDB_ROOT_USERNAME="$MONGO_ROOT_USER" \
    -e MONGO_INITDB_ROOT_PASSWORD="$MONGO_ROOT_PASS" \
    -v mongodb_data:/data/db "$MONGO_VERSION"; then
    echo "[mongodb] ERROR: $MONGO_VERSION failed to start. Not falling back to an older image:" >&2
    echo "[mongodb] an older mongod cannot open a newer volume, and the attempt itself is the hazard." >&2
    docker logs ${CONTAINER_NAME} 2>&1 | tail -20 >&2 || true
    docker rm -f ${CONTAINER_NAME} 2>/dev/null || true
    exit 1
fi
echo "[mongodb] Container started with $MONGO_VERSION"

# Check if the container is running. START_SETTLE_SECONDS is a test seam;
# the production default stays 5.
sleep "${START_SETTLE_SECONDS:-5}"
if [ "$(docker inspect -f '{{.State.Running}}' ${CONTAINER_NAME} 2>/dev/null)" == "true" ]; then
    echo "[mongodb] Ready on port ${MONGO_PORT}"
else
    echo "[mongodb] ERROR: Container failed to start properly. Logs:" >&2
    docker logs ${CONTAINER_NAME} 2>&1
    exit 1
fi

# Record the version this volume is now at. Written only after a verified
# start, so a failed attempt can never raise the floor.
mkdir -p "$(dirname "$VOLUME_VERSION_FILE")" 2>/dev/null || true
if [ -n "$REQUESTED_VERSION" ]; then
    printf '%s\n' "$REQUESTED_VERSION" > "$VOLUME_VERSION_FILE" 2>/dev/null \
        || echo "[mongodb] WARNING: could not record volume version at $VOLUME_VERSION_FILE" >&2
    echo "[mongodb] Volume version recorded: $REQUESTED_VERSION"
fi
