#!/bin/bash
# start-redis-docker.sh — start Redis via Docker with cleanup
# Called by systemd unit: redis.service

set -e

CONTAINER_NAME="atomic-redis-dev"
REDIS_PORT="6379"

echo "[redis] Pruning old Docker artifacts..."
docker system prune -f 2>/dev/null || true

# Stop and remove existing container if it exists
if [ "$(docker ps -q -f name=${CONTAINER_NAME})" ]; then
    echo "[redis] Stopping existing container..."
    docker stop ${CONTAINER_NAME} 2>/dev/null || true
fi

if [ "$(docker ps -a -q -f name=${CONTAINER_NAME})" ]; then
    echo "[redis] Removing existing container..."
    docker rm ${CONTAINER_NAME} 2>/dev/null || true
fi

echo "[redis] Starting Redis container..."
# `--appendonly yes`: Redis is a CACHE tier, but inbox delivery watermarks were stored there
# and all ten were lost to a restart on 2026-10-01T08:16:37Z (architect defect db3992b2).
# The container runs with no redis.conf, so `CONFIG SET appendonly yes` alone does NOT survive
# a container recreate — it has to be a launch argument. `appendfsync everysec` is the Redis
# default and is left explicit for the record.
if docker run --name ${CONTAINER_NAME} -p ${REDIS_PORT}:6379 -d redis:latest \
     redis-server --appendonly yes --appendfsync everysec; then
    echo "[redis] Container started."
else
    echo "[redis] ERROR: Failed to start Redis container." >&2
    exit 1
fi

# Wait for Redis to be ready
for i in $(seq 1 30); do
    if docker exec ${CONTAINER_NAME} redis-cli ping &>/dev/null; then
        echo "[redis] Ready on port ${REDIS_PORT}"
        exit 0
    fi
    sleep 1
done

echo "[redis] WARNING: Redis did not respond to ping after 30s" >&2
exit 1
