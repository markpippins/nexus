#!/bin/bash

# Script to start both Projman backend and UI without Docker

echo "🚀 Starting Projman Full Stack Application..."

# Start the backend in the background
echo "📦 Starting backend service..."
cd /mnt/c/dev/WORK/atomic/go/projman
./scripts/start-backend.sh &

# Wait a moment for the backend to start
sleep 3

# Start the UI in the foreground
echo "🌐 Starting UI service..."
cd /mnt/c/dev/WORK/atomic/go/projman/ui
./../scripts/start-ui.sh