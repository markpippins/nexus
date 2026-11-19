#!/bin/bash

# Test database connection and table creation
echo "Testing projman service database connection..."

# Start MySQL in background with correct credentials
echo "Starting MySQL..."
docker run --name test-projman-mysql -e MYSQL_ROOT_PASSWORD=rootpass -p 3307:3306 -d mysql:8.0

# Wait for MySQL to start
echo "Waiting for MySQL to start..."
sleep 10

# Set environment variables for test
export DB_HOST=localhost
export DB_PORT=3307
export DB_USER=root
export DB_PASSWORD=rootpass
export DB_NAME=projman_service

echo "Running projman service for 5 seconds to test database initialization..."
timeout 5s ./projman

# Cleanup
echo "Cleaning up..."
docker stop test-projman-mysql
docker rm test-projman-mysql

echo "Database connection test completed!"