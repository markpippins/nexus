#!/bin/bash
# Start NATS (JetStream) container — mirrors start-mongodb-docker.sh pattern.
# JetStream enabled (-js) with persistent store in the nats_data volume:
# nexus-core's mobile write-queue buffers reconciliation intents into the
# nexus.write-queue stream, so the store must survive restarts.
# Monitor API on 8222 (health/metrics), client port 4222 on the LAN interface.

if [ "$(docker ps -q -f name=atomic-nats)" ]; then
    echo "Stopping existing NATS container..."
    docker stop atomic-nats
fi

if [ "$(docker ps -a -q -f name=atomic-nats)" ]; then
    echo "Removing existing NATS container..."
    docker rm atomic-nats
fi

echo "Starting NATS container..."
docker run -d --name atomic-nats \
  -p 4222:4222 -p 8222:8222 \
  -v nats_data:/data \
  --restart unless-stopped \
  nats:2-alpine -js -m 8222 --store_dir /data

sleep 3
if [ "$(docker inspect -f '{{.State.Running}}' atomic-nats 2>/dev/null)" == "true" ]; then
    echo "NATS is running: client 4222, monitor 8222, JetStream store nats_data:/data"
    docker logs atomic-nats 2>&1 | tail -5
else
    echo "Error: NATS container failed to start properly"
    echo "Check logs with: docker logs atomic-nats"
    docker logs atomic-nats 2>&1
    exit 1
fi
