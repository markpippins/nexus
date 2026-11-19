# Python Broker Gateway - Service Registry Integration

## Overview

The python-broker-gateway now registers with the Spring Boot service-registry and sends periodic heartbeats to maintain its registration status.

## How It Works

### Auto-Registration on Startup
1. On application startup, the service automatically registers with the service-registry
2. It sends registration details including operations it supports, endpoint URL, and metadata
3. After successful registration, it starts sending periodic heartbeats

### Configuration
The service uses the following environment variables:

- `SERVICE_REGISTRY_URL`: URL of the service-registry registry (default: `http://localhost:8085/api/registry`)
- `SERVICE_HOST`: Host address of this service (default: `localhost`)
- `PORT`: Port this service runs on (default: `8050`)
- `HEARTBEAT_INTERVAL_SECONDS`: Interval between heartbeats in seconds (default: `30`)

### Service Registration Details
- **Service Name**: `python-broker-gateway`
- **Operations**: Currently registers `["test"]` operation
- **Framework**: `Python-FastAPI`
- **Health Check**: `/health` endpoint

## Implementation

### Registration Process
1. During the FastAPI `startup_event`, the service:
   - Initializes the RegistryClient
   - Constructs the service endpoint URL from environment variables
   - Registers with the service-registry via POST to `/api/registry/register`
   - Starts the heartbeat loop if registration succeeds

### Heartbeat Process
- A background asyncio task sends a heartbeat every 30 seconds (configurable)
- Heartbeat is sent via POST to `/api/registry/heartbeat/python-broker-gateway`
- If heartbeat fails, logs a warning (service may need to re-register)

### Shutdown Process
- During the FastAPI `shutdown_event`, the service:
  - Stops the heartbeat loop
  - Deregisters from the service-registry
  - Closes the HTTP client

## API Endpoints Used

### Registration
- **POST** `/api/registry/register`
- Payload includes service name, operations, endpoint, etc.

### Heartbeat
- **POST** `/api/registry/heartbeat/{serviceName}`
- Keeps the service registration alive

### Deregistration
- **POST** `/api/registry/deregister/{serviceName}`
- Removes the service from registry on shutdown