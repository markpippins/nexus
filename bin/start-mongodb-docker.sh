#!/bin/bash
# start-mongodb-docker.sh — start MongoDB via Docker with cleanup
# Called by systemd unit: mongodb.service

set -e

CONTAINER_NAME="atomic-mongodb"
MONGO_PORT="27017"

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

# Stop and remove existing container if it exists
if [ "$(docker ps -q -f name=${CONTAINER_NAME})" ]; then
    echo "[mongodb] Stopping existing container..."
    docker stop ${CONTAINER_NAME} 2>/dev/null || true
fi

if [ "$(docker ps -a -q -f name=${CONTAINER_NAME})" ]; then
    echo "[mongodb] Removing existing container..."
    docker rm ${CONTAINER_NAME} 2>/dev/null || true
fi

echo "[mongodb] Starting MongoDB container..."
# Try multiple versions for ARM64 compatibility
# Clean up container after each failed attempt to avoid name collision
MONGO_VERSIONS=("mongo:4.4.18" "mongo:4.2.18" "mongo:4.0.28")
STARTED=false
for ver in "${MONGO_VERSIONS[@]}"; do
    echo "[mongodb] Trying $ver ..."
    if docker run -d --name ${CONTAINER_NAME} -p "${MONGO_BIND_ADDR}:${MONGO_PORT}:27017" \
        -e MONGO_INITDB_ROOT_USERNAME="$MONGO_ROOT_USER" \
        -e MONGO_INITDB_ROOT_PASSWORD="$MONGO_ROOT_PASS" \
        -v mongodb_data:/data/db "$ver"; then
        echo "[mongodb] Container started with $ver"
        STARTED=true
        break
    else
        echo "[mongodb] $ver failed, cleaning up..."
        docker rm -f ${CONTAINER_NAME} 2>/dev/null || true
    fi
done

if [ "$STARTED" != "true" ]; then
    echo "[mongodb] ERROR: Failed to start MongoDB container with all version attempts." >&2
    exit 1
fi

# Check if the container is running
sleep 5
if [ "$(docker inspect -f '{{.State.Running}}' ${CONTAINER_NAME} 2>/dev/null)" == "true" ]; then
    echo "[mongodb] Ready on port ${MONGO_PORT}"
else
    echo "[mongodb] ERROR: Container failed to start properly. Logs:" >&2
    docker logs ${CONTAINER_NAME} 2>&1
    exit 1
fi
