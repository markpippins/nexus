#!/bin/bash

# Test runner for file-system-server

echo "Starting file-system-server tests..."

# Start the server in the background
echo "Starting server..."
FS_ROOT_DIR=/tmp/test-fs-root npx tsx fs-serv.ts > server.log 2>&1 &
SERVER_PID=$!

# Wait for server to start
sleep 3

# Check if server is running
if kill -0 $SERVER_PID 2>/dev/null; then
    echo "Server is running with PID $SERVER_PID"
else
    echo "Failed to start server"
    cat server.log
    exit 1
fi

# Run the tests
echo "Running tests..."
npx tsx test-fs-server.ts

TEST_RESULT=$?

# Clean up
echo "Stopping server..."
kill $SERVER_PID

if [ $TEST_RESULT -eq 0 ]; then
    echo "All tests passed!"
    exit 0
else
    echo "Some tests failed!"
    exit 1
fi