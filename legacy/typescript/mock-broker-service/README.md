# Mock Service Broker

This is a mock implementation of the Spring Boot Service Broker API that your frontend applications expect.

## Purpose

The react-ts-servicebroker (and other servicebroker apps) require a backend API running on port 8080. This mock server provides that API for development purposes.

## Quick Start

1. **Install dependencies:**
   ```bash
   cd mock-service-broker
   npm install
   ```

2. **Start the server:**
   ```bash
   npm start
   ```

3. **Verify it's running:**
   Open http://localhost:8080/health in your browser

## API Endpoints

### POST /api/broker/submitRequest
Accepts service broker requests with the following format:
```json
{
  "service": "userService",
  "operation": "getById",
  "params": {"id": 1},
  "requestId": "client-123456"
}
```

### Supported Operations

**userService:**
- `getById` - Get user by ID: `{"id": 1}`
- `create` - Create new user: `{"user": {"name": "John", "email": "john@example.com"}}`
- `list` - Get all users: `{}`

### Response Format
```json
{
  "ok": true,
  "data": {...},
  "errors": [],
  "requestId": "client-123456", 
  "ts": "2025-09-28T..."
}
```

## Mock Data

The server starts with 2 sample users:
- ID 1: John Doe (john@example.com)
- ID 2: Jane Smith (jane@example.com)

New users will be assigned incrementing IDs starting from 3.

## CORS

CORS is enabled for all origins to support frontend development.# Mock Service Broker

This is a mock implementation of the Spring Boot Service Broker API that your frontend applications expect.

## Purpose

The react-ts-servicebroker (and other servicebroker apps) require a backend API running on port 8080. This mock server provides that API for development purposes.

## Quick Start

1. **Install dependencies:**
   ```bash
   cd mock-service-broker
   npm install
   ```

2. **Start the server:**
   ```bash
   npm start
   ```

3. **Verify it's running:**
   Open http://localhost:8080/health in your browser

## API Endpoints

### POST /api/broker/submitRequest
Accepts service broker requests with the following format:
```json
{
  "service": "userService",
  "operation": "getById",
  "params": {"id": 1},
  "requestId": "client-123456"
}
```

### Supported Operations

**userService:**
- `getById` - Get user by ID: `{"id": 1}`
- `create` - Create new user: `{"user": {"name": "John", "email": "john@example.com"}}`
- `list` - Get all users: `{}`

### Response Format
```json
{
  "ok": true,
  "data": {...},
  "errors": [],
  "requestId": "client-123456", 
  "ts": "2025-09-28T..."
}
```

## Mock Data

The server starts with 2 sample users:
- ID 1: John Doe (john@example.com)
- ID 2: Jane Smith (jane@example.com)

New users will be assigned incrementing IDs starting from 3.

## CORS

CORS is enabled for all origins to support frontend development.