# Polyglot Service Integration Guide

## Overview

The Atomic Platform provides comprehensive support for polyglot microservices, allowing services written in different programming languages and frameworks to coexist and communicate seamlessly through the broker gateway system.

This guide covers how to integrate new services into the platform, regardless of the technology stack.

## Architecture

```
┌─────────────────┐     ┌─────────────────────────────┐     ┌──────────────────┐
│  Clients        │────▶│  Broker Gateway Proxy       │────▶│  Broker Gateway  │
│  (Nexus UI,     │     │  (AdonisJS - Port 8080)     │     │  (Spring Boot)   │
│   SDKs, etc.)   │     │  - Rate Limiting            │     │  Port 8081       │
└─────────────────┘     │  - Request Logging          │     └──────────────────┘
                         │  - Host Registration        │              │
                         └─────────────────────────────┘              │
                                   │                                  │
                                   ▼                                  │
                         ┌─────────────────┐                         │
                         │  Host-Server    │◀────────────────────────┘
                         │  (Registry)     │
                         │  Port 8085      │
                         └─────────────────┘
                                   │
                                   ▼
                         ┌─────────────────────────────────────────────┐
                         │           Polyglot Services                 │
                         │  ┌─────────┐ ┌─────────┐ ┌─────────────┐   │
                         │  │ Spring  │ │ Quarkus │ │   Helidon   │   │
                         │  │  Boot   │ │         │ │             │   │
                         │  └─────────┘ └─────────┘ └─────────────┘   │
                         │  ┌─────────┐ ┌─────────┐ ┌─────────────┐   │
                         │  │ Node.js │ │   Go    │ │   Python    │   │
                         │  │         │ │         │ │             │   │
                         │  └─────────┘ └─────────┘ └─────────────┘   │
                         └─────────────────────────────────────────────┘
```

## Service Registration

All services register with the host-server using the same REST API:

### Registration Endpoint
```
POST http://localhost:8085/api/registry/register
```

### Registration Payload
```json
{
  "serviceName": "my-microservice",
  "operations": ["getUserData", "updateProfile", "deleteUser"],
  "endpoint": "http://localhost:3001",
  "healthCheck": "health",
  "metadata": {
    "version": "1.0.0",
    "description": "User management service"
  },
  "framework": "FastAPI",
  "port": 3001,
  "hostedServices": []
}
```

### Required Fields
- **serviceName**: Unique service identifier
- **operations**: Array of operation names the service provides
- **endpoint**: Service base URL
- **healthCheck**: Health check endpoint path
- **framework**: Framework name (used for grouping in UI)
- **port**: Service port number

### Optional Fields
- **metadata**: Additional service information
- **hostedServices**: For proxy services that host other services
- **version**: Service version

## Framework-Specific Integration

### Java/Spring Boot Services

#### Automatic Registration
Spring Boot services can use automatic registration:

```java
@Service
@BrokerAutoRegistration
public class UserService {
    
    @BrokerOperation("getUserData")
    public UserResponse getUserData(GetUserRequest request) {
        // Implementation
    }
    
    @BrokerOperation("updateProfile")
    public UpdateProfileResponse updateProfile(UpdateProfileRequest request) {
        // Implementation
    }
}
```

#### Manual Registration
```java
@RestController
public class ServiceRegistrationController {
    
    @Autowired
    private HostServerClient hostServerClient;
    
    @PostConstruct
    public void registerService() {
        ServiceRegistration registration = ServiceRegistration.builder()
            .serviceName("user-service")
            .operations(Arrays.asList("getUserData", "updateProfile"))
            .endpoint("http://localhost:8083")
            .healthCheck("actuator/health")
            .framework("Spring Boot")
            .port(8083)
            .metadata(Map.of("version", "1.0.0"))
            .build();
            
        hostServerClient.registerService(registration);
    }
}
```

### Node.js Services

#### Using the SDK
```javascript
const { createClient, ServiceDetails } = require('./atomic_broker_sdk');

const client = createClient({ 
    gatewayUrl: 'http://localhost:8080',
    hostServerUrl: 'http://localhost:8085'
});

const service = new ServiceDetails({
    serviceName: 'nodejs-user-service',
    endpoint: 'http://localhost:3001',
    healthCheck: 'health',
    framework: 'Express',
    operations: ['getUserData', 'updateProfile'],
    metadata: { version: '1.0.0' },
    port: 3001
});

// Register the service
await client.registerService(service);
```

#### Express Integration Example
```javascript
const express = require('express');
const { createClient } = require('./atomic_broker_sdk');

const app = express();
const client = createClient();

// Registration endpoint
app.post('/users/:id', async (req, res) => {
    // This operation is now available through the broker
    const userId = req.params.id;
    const userData = await getUserFromDatabase(userId);
    res.json(userData);
});

// Health check endpoint
app.get('/health', (req, res) => {
    res.json({ status: 'healthy', timestamp: new Date().toISOString() });
});

// Register service on startup
async function startService() {
    try {
        await client.registerService({
            serviceName: 'user-service-express',
            endpoint: 'http://localhost:3001',
            healthCheck: 'health',
            framework: 'Express',
            operations: ['getUserData', 'updateProfile'],
            port: 3001
        });
        
        app.listen(3001, () => {
            console.log('User service running on port 3001');
        });
    } catch (error) {
        console.error('Failed to register service:', error);
    }
}

startService();
```

### Python Services

#### Using the SDK
```python
from fastapi import FastAPI
from atomic_broker_sdk import create_client, ServiceDetails
import uvicorn

app = FastAPI()
client = create_client(
    gateway_url="http://localhost:8080",
    host_server_url="http://localhost:8085"
)

# Service registration on startup
@app.on_event("startup")
async def startup_event():
    service = ServiceDetails(
        service_name="user-service-fastapi",
        endpoint="http://localhost:3002",
        health_check="health",
        framework="FastAPI",
        operations=["getUserData", "updateProfile"],
        metadata={"version": "1.0.0"},
        port=3002
    )
    await client.register_service(service)

# Operation endpoint
@app.post("/users/{user_id}")
async def get_user_data(user_id: str, request: dict):
    # This operation is available through the broker
    user_data = await get_user_from_database(user_id)
    return user_data

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3002)
```

### Go Services

#### Using the SDK
```go
package main

import (
    "log"
    "net/http"
    
    "github.com/gin-gonic/gin"
    broker "github.com/atomic/broker-sdk"
)

func main() {
    client := broker.NewClient("http://localhost:8080", "http://localhost:8085")
    
    // Register service
    service := broker.ServiceDetails{
        ServiceName: "user-service-go",
        Endpoint:    "http://localhost:3003",
        HealthCheck: "health",
        Framework:   "Gin",
        Operations:  []string{"getUserData", "updateProfile"},
        Port:        3003,
    }
    
    success, err := client.RegisterService(service)
    if err != nil {
        log.Fatal("Failed to register service:", err)
    }
    if !success {
        log.Fatal("Service registration failed")
    }
    
    r := gin.Default()
    
    // Operation endpoint
    r.POST("/users/:id", func(c *gin.Context) {
        userID := c.Param("id")
        userData := getUserFromDatabase(userID)
        c.JSON(200, userData)
    })
    
    // Health check endpoint
    r.GET("/health", func(c *gin.Context) {
        c.JSON(200, gin.H{
            "status":    "healthy",
            "timestamp": time.Now().Format(time.RFC3339),
        })
    })
    
    log.Println("User service running on port 3003")
    r.Run(":3003")
}
```

## Service Discovery & Invocation

Once registered, services can be discovered and invoked through the broker gateway:

### Service Discovery
```javascript
// Find service by operation
const services = await client.find_services_by_operation("getUserData");
```

### Service Invocation
```javascript
// Invoke operation through broker
const response = await client.invoke_operation(
    "getUserData", 
    { userId: "123" }
);
```

## Heartbeat & Health Monitoring

Services must maintain a heartbeat with the host-server:

### Heartbeat Endpoint
```
POST http://localhost:8085/api/registry/heartbeat/{serviceName}
```

### Heartbeat Implementation (Node.js)
```javascript
// Send heartbeat every 30 seconds
setInterval(async () => {
    try {
        await client.send_heartbeat('user-service-express');
        console.log('Heartbeat sent successfully');
    } catch (error) {
        console.error('Heartbeat failed:', error);
    }
}, 30000);
```

### SDK-Based Heartbeat
All SDKs provide automatic heartbeat functionality:

```python
# Python SDK
client = create_client(gateway_url="http://localhost:8080")
client.start_heartbeat("my-service", interval=30)

# Node.js SDK  
client.startHeartbeat("my-service", 30000);

# Go SDK
client.StartHeartbeat("my-service", 30*time.Second)
```

## Best Practices

### 1. Service Design
- **Idempotent Operations**: Design operations to be idempotent
- **Clear Interfaces**: Define clear input/output contracts
- **Error Handling**: Implement comprehensive error handling
- **Logging**: Use structured logging with correlation IDs

### 2. Health Checks
- **Health Endpoints**: Implement `/health` endpoints
- **Dependency Checks**: Check database/external service health
- **Response Format**: Use consistent health check format:
  ```json
  {
    "status": "healthy|degraded|unhealthy",
    "timestamp": "2024-01-01T00:00:00Z",
    "checks": {
      "database": "healthy",
      "external_service": "healthy"
    }
  }
  ```

### 3. Configuration
- **Environment-Specific**: Use environment-specific configurations
- **Service Discovery**: Don't hardcode service URLs
- **Timeouts**: Configure appropriate timeouts for operations

### 4. Security
- **Authentication**: Implement service-to-service authentication
- **Authorization**: Use operation-level authorization
- **Input Validation**: Validate all inputs at service boundaries

### 5. Monitoring
- **Metrics**: Export metrics for monitoring (Prometheus, etc.)
- **Tracing**: Implement distributed tracing
- **Logging**: Include correlation IDs in all logs

## Service Communication Patterns

### 1. Request/Response
Standard synchronous communication through the broker:

```javascript
const response = await client.invoke_operation(
    "getUserData", 
    { userId: "123" }
);
```

### 2. Event-Driven
Asynchronous event patterns:

```javascript
// Publish event
await client.publish_event("user.created", { userId: "123", email: "user@example.com" });

// Subscribe to events
client.subscribe("user.created", (event) => {
    console.log('User created:', event.data);
});
```

### 3. Service Composition
Combine multiple services:

```javascript
const user = await client.invoke_operation("getUserData", { userId: "123" });
const permissions = await client.invoke_operation("getUserPermissions", { userId: "123" });
const profile = await client.invoke_operation("getUserProfile", { userId: "123" });
```

## Deployment Considerations

### 1. Containerization
```dockerfile
# Node.js service Dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
EXPOSE 3001
CMD ["npm", "start"]
```

### 2. Kubernetes Deployment
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: user-service
spec:
  replicas: 3
  selector:
    matchLabels:
      app: user-service
  template:
    metadata:
      labels:
        app: user-service
    spec:
      containers:
      - name: user-service
        image: user-service:latest
        ports:
        - containerPort: 3001
        env:
        - name: BROKER_GATEWAY_URL
          value: "http://broker-gateway:8080"
        - name: HOST_SERVER_URL
          value: "http://host-server:8085"
```

### 3. Service Mesh Integration
The platform integrates with service mesh patterns:
- **Service Discovery**: Automatic service registration and discovery
- **Load Balancing**: Multiple service instances support
- **Circuit Breaker**: Built-in circuit breaker patterns
- **Health Monitoring**: Real-time health status

## Troubleshooting

### Common Issues

#### 1. Service Registration Fails
**Symptoms**: Service doesn't appear in Nexus UI
**Solutions**:
- Check host-server connectivity: `curl http://localhost:8085/api/registry/services`
- Verify registration payload format
- Check firewall rules
- Review service logs

#### 2. Operations Not Available
**Symptoms**: Operations not found when invoking
**Solutions**:
- Verify service registration completed
- Check operation names match exactly
- Review broker gateway logs
- Test operation endpoint directly

#### 3. Health Check Failures
**Symptoms**: Service appears unhealthy in Nexus
**Solutions**:
- Check health endpoint accessibility: `curl http://localhost:3001/health`
- Verify heartbeat mechanism is working
- Check service dependencies
- Review service logs for errors

### Debugging Tools

#### 1. Host Server API
```bash
# List all registered services
curl http://localhost:8085/api/registry/services

# Get service details
curl http://localhost:8085/api/registry/services/{serviceName}/details

# Find services by operation
curl http://localhost:8085/api/registry/services/by-operation/{operationName}
```

#### 2. Broker Gateway API
```bash
# Test operation invocation
curl -X POST http://localhost:8081/api/operations/{operationName} \
  -H "Content-Type: application/json" \
  -d '{"data": {"key": "value"}}'
```

#### 3. Nexus UI
- Service mesh visualization
- Real-time service status
- Operation execution
- Log viewing

## Migration Guide

### From Direct Service Communication
1. **Replace HTTP clients** with SDK calls
2. **Add service registration** to startup
3. **Implement health checks**
4. **Update configuration** for broker gateway URLs
5. **Test with existing services** before migration

### From Legacy Systems
1. **Implement adapter pattern** for legacy services
2. **Gradual migration** of operations
3. **Parallel operation** during transition
4. **Monitoring** of migration progress
5. **Fallback mechanisms** for reliability

## Conclusion

The Atomic Platform's polyglot service integration provides:
- **Language Flexibility**: Use the best language for each service
- **Unified Communication**: Consistent APIs across all services
- **Dynamic Discovery**: Runtime service discovery and routing
- **Operational Excellence**: Comprehensive monitoring and management
- **Gradual Migration**: Path for existing systems to adopt the platform

By following this guide, you can successfully integrate services written in any programming language into the Atomic Platform, taking advantage of its unified service mesh capabilities while maintaining the benefits of polyglot architectures.