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
# AOF is deliberately NOT enabled here.
#
# It was added while fixing architect defect db3992b2 (inbox pointers were Redis-resident and
# all ten were lost to a restart on 2026-10-01T08:16:37Z), on the reasoning that a restart-safe
# cache is worth having. It is not, now that the pointers live in Postgres
# (migration 071, nebula.role_inbox_pointers): every key in this Redis is PG-backed, rebuildable,
# or explicitly ephemeral -- role leases, ticket claims and clock sessions are in PG; the
# procedure-card index rebuilds from PG; sse/event-buffer costs backlog, not record delivery;
# service heartbeats are ephemeral by design.
#
# Worse, it is FALSE durability. The container mounts no volume, so /data is the overlay
# filesystem: the AOF survives a process restart and is destroyed by `docker rm`. That is the
# worst of both -- it reads as protection that does not exist, and it costs write amplification
# plus unbounded growth on a disk already at 95%.
#
# If this container ever needs a durable volume, that is an infra change to make deliberately
# with the disk headroom in mind -- not a flag smuggled in via a launcher script.
if docker run --name ${CONTAINER_NAME} -p ${REDIS_PORT}:6379 -d redis:latest; then
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
