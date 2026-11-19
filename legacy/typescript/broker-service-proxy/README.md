# Broker Service Proxy

A TypeScript-based proxy server that forwards requests to the broker gateway service. This proxy acts as an intermediary between clients and the broker gateway, providing a unified interface for service requests.

## Overview

The Broker Service Proxy receives requests in the same format as the broker gateway and forwards them to the actual broker gateway service. It maintains the same API contract, making it transparent to clients.

## Features

- **Transparent Proxying**: Forwards requests to the broker gateway while maintaining the same API contract
- **Configuration**: Supports configuration of listening port and broker gateway URL via environment variables
- **Error Handling**: Properly handles and forwards error responses from the broker gateway
- **Health Check**: Provides a health check endpoint at `/health`

## API Endpoints

- `POST /api/broker/submitRequest` - Proxies requests to the broker gateway
- `GET /health` - Health check endpoint

## Environment Variables

- `BROKER_PROXY_PORT` - Port for the proxy server to listen on (default: 3333)
- `HOST` - Host for the proxy server to bind to (default: 0.0.0.0)
- `BROKER_GATEWAY_URL` - URL of the broker gateway to forward requests to (default: http://localhost:8080)

## Usage

### Development

```bash
npm run dev
```

### Production

```bash
npm run build
npm start
```

## Configuration

Create a `.env` file based on `.env.example` to configure your environment variables:

```bash
cp .env.example .env
```

Then modify the values in `.env` to match your setup.

## Request Format

The proxy expects requests in the same format as the broker gateway:

```json
{
  "service": "user-service",
  "operation": "getUser",
  "params": {
    "id": "123"
  },
  "requestId": "unique-request-id"
}
```

## Architecture

```
Client -> Proxy Server -> Broker Gateway -> Backend Services
```