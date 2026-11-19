# Nexus Broker Gateway Node.js SDK

Lightweight Node.js client for interacting with Nexus Broker Gateway services.

## Installation

```bash
npm install axios
```

## Quick Start

```javascript
const { createClient, ServiceDetails } = require('./nexus_broker_sdk');

// Create client
const client = createClient({
    gatewayUrl: 'http://localhost:8080',
    hostServerUrl: 'http://localhost:8085'
});

// Example: Register a service
const service = new ServiceDetails({
    serviceName: 'nodejs-microservice',
    endpoint: 'http://localhost:3002',
    healthCheck: 'health',
    framework: 'Express'
});

(async () => {
    const success = await client.registerService(service);
    if (success) {
        console.log('Service registered successfully!');
    } else {
        console.log('Service registration failed');
    }
})();

// Example: Invoke a service operation
(async () => {
    const response = await client.invokeOperation(
        'getUserRegistrationForToken',
        { token: 'sample-token-123' }
    );

    if (response.success) {
        console.log('Success:', response.data);
    } else {
        console.error('Error:', response.errors);
    }
})();

// Example: Check service health
(async () => {
    const isHealthy = await client.healthCheck('loginService');
    console.log(`Login service healthy: ${isHealthy}`);
})();
```

## API Reference

### BrokerGatewayClient

Main client class for interacting with the broker gateway.

#### Constructor

```javascript
new BrokerGatewayClient(options)
```

**Options:**
- `gatewayUrl`: URL of the broker gateway (default: "http://localhost:8080")
- `hostServerUrl`: URL of the host server for service discovery (default: "http://localhost:8085")

### Methods

##### async discoverService(operation) → Promise<ServiceDetails|null>

Find a service that can handle the specified operation.

**Parameters:**
- `operation`: Operation name (e.g., "getUserRegistrationForToken")

**Returns:** `ServiceDetails` if found, `null` otherwise

```javascript
const service = await client.discoverService('getUserRegistrationForToken');
if (service) {
    console.log(`Found service: ${service.serviceName} at ${service.endpoint}`);
}
```

##### async getServiceDetails(serviceName) → Promise<ServiceDetails|null>

Get detailed information about a specific service.

**Parameters:**
- `serviceName`: Name of the service

**Returns:** `ServiceDetails` if found, `null` otherwise

```javascript
const details = await client.getServiceDetails('loginService');
if (details) {
    console.log(`Service endpoint: ${details.endpoint}`);
}
```

##### async invokeOperation(operation, params, serviceName) → Promise<BrokerResponse>

Invoke an operation on a service through the broker gateway.

**Parameters:**
- `operation`: Operation name to invoke
- `params`: Parameters for the operation
- `serviceName`: Optional service name (discovered if not provided)

**Returns:** `BrokerResponse` with operation results

```javascript
const response = await client.invokeOperation(
    'getUserRegistrationForToken',
    { token: 'user-token-123' },
    'loginService'  // Optional, auto-discovered if not provided
);

if (response.success) {
    console.log('Result:', response.data);
} else {
    console.error('Error:', response.errors);
}
```

##### async healthCheck(serviceName) → Promise<boolean>

Perform health check on a specific service.

**Parameters:**
- `serviceName`: Name of the service to check

**Returns:** `true` if healthy, `false` otherwise

```javascript
const isHealthy = await client.healthCheck('loginService');
console.log(`Login service healthy: ${isHealthy}`);
```

##### async registerService(serviceDetails) → Promise<boolean>

Register a new service with the broker gateway.

**Parameters:**
- `serviceDetails`: ServiceDetails object or plain object containing service information

**Returns:** `true` if registration successful, `false` otherwise

```javascript
const serviceDetails = {
    serviceName: 'my-service',
    endpoint: 'http://localhost:3000',
    healthCheck: 'health',
    framework: 'Express'
};

const success = await client.registerService(serviceDetails);
if (success) {
    console.log('Service registered!');
}
```

##### async getGatewayHealth() → Promise<BrokerResponse>

Check the health of the broker gateway itself.

**Returns:** `BrokerResponse` with gateway health status

```javascript
const response = await client.getGatewayHealth();
if (response.success) {
    console.log('Gateway is healthy');
} else {
    console.log('Gateway error:', response.errors);
}
```

## Data Models

### ServiceDetails

Contains service information from the broker gateway.

```javascript
class ServiceDetails {
    constructor(data) {
        this.serviceName = data.serviceName || '';
        this.endpoint = data.endpoint || '';
        this.healthCheck = data.healthCheck || null;
        this.framework = data.framework || null;
        this.status = data.status || null;
        this.operations = data.operations || null;
    }
}
```

### BrokerResponse

Response from broker gateway operations.

```javascript
class BrokerResponse {
    constructor(options = {}) {
        this.success = options.success || false;
        this.data = options.data || null;
        this.errors = options.errors || null;
        this.statusCode = options.statusCode || 200;
        this.rawResponse = options.rawResponse || null;
    }
}
```

## Error Handling

All SDK operations return structured error information:

```javascript
if (!response.success) {
    response.errors.forEach(error => {
        console.error(`Error ${error.code}: ${error.message}`);
    });
}
```

Common error codes:
- `SERVICE_NOT_FOUND`: No service found for operation
- `SERVICE_DETAILS_NOT_FOUND`: Could not get service details
- `OPERATION_FAILED`: HTTP operation failed
- `CLIENT_ERROR`: Client-side error (network, parsing, etc.)
- `GATEWAY_UNHEALTHY`: Gateway health check failed

## Integration Examples

### Express.js

```javascript
const express = require('express');
const { createClient } = require('./nexus_broker_sdk');

const app = express();
const client = createClient();

app.post('/users/token', async (req, res) => {
    const response = await client.invokeOperation(
        'getUserRegistrationForToken',
        req.body
    );

    if (response.success) {
        res.json(response.data);
    } else {
        res.status(500).json({ error: response.errors });
    }
});

app.listen(3000);
```

### Koa.js

```javascript
const Koa = require('koa');
const { createClient } = require('./nexus_broker_sdk');

const app = new Koa();
const client = createClient();

app.use(async (ctx) => {
    if (ctx.path === '/users/token' && ctx.method === 'POST') {
        const response = await client.invokeOperation(
            'getUserRegistrationForToken',
            ctx.request.body
        );

        if (response.success) {
            ctx.body = response.data;
        } else {
            ctx.status = 500;
            ctx.body = { error: response.errors };
        }
    }
});

app.listen(3000);
```

### AWS Lambda

```javascript
const { createClient } = require('./nexus_broker_sdk');

exports.handler = async (event) => {
    const client = createClient();
    
    const response = await client.invokeOperation(
        'getUserRegistrationForToken',
        JSON.parse(event.body)
    );

    return {
        statusCode: response.statusCode,
        body: JSON.stringify(response.success ? response.data : { error: response.errors })
    };
};
```