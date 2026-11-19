#!/bin/bash

# Script to start the Projman UI without Docker

echo "🚀 Starting Projman UI..."

# Navigate to the UI directory
cd /mnt/c/dev/WORK/atomic/go/projman/ui

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is not installed. Please install Node.js before running this service."
    exit 1
fi

# Check if Bun is installed
if command -v bun &> /dev/null; then
    echo "📦 Using Bun to install dependencies..."
    bun install
else
    # Fallback to npm if Bun is not available
    echo "📦 Using npm to install dependencies..."
    npm install
fi

# Start the Angular development server
echo "🏃 Starting the UI development server..."
if command -v bun &> /dev/null; then
    bun run start
else
    npm run start
fi