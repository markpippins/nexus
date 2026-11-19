#!/bin/bash

# Script to start the Projman backend service without Docker

echo "🚀 Starting Projman Backend Service..."

# Check if MySQL is running
if ! pgrep mysqld > /dev/null; then
    echo "❌ MySQL is not running. Please start MySQL before running this service."
    echo "You can start MySQL with: sudo systemctl start mysql"
    exit 1
fi

echo "✅ MySQL is running"

# Navigate to the project directory
cd /mnt/c/dev/WORK/atomic/go/projman

# Install dependencies
echo "📦 Installing Go dependencies..."
go mod tidy

# Build the project
echo "🔨 Building the project..."
go build -o projman .

# Run the service
echo "🏃 Running the Projman service..."
./projman