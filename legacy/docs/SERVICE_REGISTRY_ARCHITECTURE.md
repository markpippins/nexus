# Service Registry Architecture

## Design Decision: Integrated vs Standalone Registry

### The Refactoring

We refactored the service-registry from a **standalone Spring Boot application** to an **integrated service** within broker-gateway that follows the `@BrokerOperation` pattern.

### Why This is Better

#### 1. **Architectural Consistency**
Every other service in the platform uses `@BrokerOperation` annotations:
- `RestFsService` - file operations
- `UserAccessService` - user management  
- `GoogleSearchService` - search operations
- `LoginService` - authentication

Service-registry should follow the same pattern, not be a special snowflake with its own REST API.

#### 2. **Single Entry Point**
```
Before: External Service → Service Registry (8090) → [separate service]
After:  External Service → Broker Gateway (8080) → Service Registry (component)
```

All traffic flows through broker-gateway. No bypassing the broker pattern.

#### 3. **Unified Protocol**
External services register using the standard `ServiceRequest` format:

```json
{
  "service": "serviceRegistry",
  "operation": "register",
  "params": {
    "registration": {
      "serviceName": "googleSearchService",
      "operations": ["simpleSearch"],
      "endpoint": "http://localhost:4050",
      "healthCheck": "http://localhost:4050/api/health"
    }
  }
}
```

This is the same protocol used for all other operations.

#### 4. **Simplified Deployment**
- **Before**: 2 services (broker-gateway + service-registry)
- **After**: 1 service (broker-gateway with registry component)

Less infrastructure, fewer ports, simpler Docker Compose.

#### 5. **Cross-Cutting Concerns**
Registration requests now benefit from:
- Authentication/authorization (if added to broker)
- Rate limiting
- Request logging
- Distributed tracing
- Circuit breaking

All handled in one place.

## Implementation Details

### Service Registry Component

Located in `spring/service-registry/`, packaged as a library (not a standalone app):

```java
@Service("serviceRegistry")
public class RegistryService {
    
    @BrokerOperation("register")
    public Map<String, String> register(
        @BrokerParam("registration") ServiceRegistration registration) {
        // Register external service
    }
    
    @BrokerOperation("findByOperation")
    public ServiceRegistration findByOperation(
        @BrokerParam("operation") String operation) {
        // Lookup service by operation name
    }
    
    @BrokerOperation("getAllServices")
    public List<ServiceRegistration> getAllServices() {
        // List all registered services
    }
}
```

### Broker Gateway Integration

The broker-gateway includes service-registry in its component scan:

```java
@ComponentScan(basePackages = {
    "com.angrysurfer.atomic.broker",
    "com.angrysurfer.atomic.registry",  // ← Service registry
    // ... other packages
})
```

### External Service Registration

Moleculer services register via the broker protocol:

```typescript
const serviceRequest = {
  service: "serviceRegistry",
  operation: "register",
  params: {
    registration: {
      serviceName: "googleSearchService",
      operations: ["simpleSearch"],
      endpoint: "http://localhost:4050",
      healthCheck: "http://localhost:4050/api/health"
    }
  }
};

await axios.post('http://localhost:8080/api/broker/submitRequest', serviceRequest);
```

## Benefits for Enterprise Pattern Exploration

This architecture demonstrates:

1. **Service Mesh Concepts**: Centralized routing and service discovery
2. **API Gateway Pattern**: Single entry point for all services
3. **Sidecar Pattern**: External services can register themselves
4. **Polyglot Microservices**: Java, Node.js, future: Python, Go, etc.
5. **Dynamic Service Discovery**: Services register/deregister at runtime

## Comparison to Industry Standards

| Pattern | Our Implementation | Industry Example |
|---------|-------------------|------------------|
| Service Registry | Integrated component | Consul, Eureka |
| API Gateway | Broker Gateway | Kong, Apigee |
| Service Mesh | Broker routing | Istio, Linkerd |
| Protocol | ServiceRequest/Response | gRPC, REST |

## Future Enhancements

1. **Health Checking**: Periodic health checks of registered services
2. **Load Balancing**: Route to multiple instances of same service
3. **Circuit Breaking**: Fail fast when services are down
4. **Service Versioning**: Support multiple versions of same service
5. **Persistent Registry**: Store registrations in MongoDB

## Testing the Registry

### Register a Service
```bash
curl -X POST http://localhost:8080/api/broker/submitRequest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "serviceRegistry",
    "operation": "register",
    "params": {
      "registration": {
        "serviceName": "testService",
        "operations": ["testOp"],
        "endpoint": "http://localhost:5000",
        "healthCheck": "http://localhost:5000/health"
      }
    }
  }'
```

### List All Services
```bash
curl -X POST http://localhost:8080/api/broker/submitRequest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "serviceRegistry",
    "operation": "getAllServices",
    "params": {}
  }'
```

### Find Service by Operation
```bash
curl -X POST http://localhost:8080/api/broker/submitRequest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "serviceRegistry",
    "operation": "findByOperation",
# Service Registry Architecture

## Design Decision: Integrated vs Standalone Registry

### The Refactoring

We refactored the service-registry from a **standalone Spring Boot application** to an **integrated service** within broker-gateway that follows the `@BrokerOperation` pattern.

### Why This is Better

#### 1. **Architectural Consistency**
Every other service in the platform uses `@BrokerOperation` annotations:
- `RestFsService` - file operations
- `UserAccessService` - user management  
- `GoogleSearchService` - search operations
- `LoginService` - authentication

Service-registry should follow the same pattern, not be a special snowflake with its own REST API.

#### 2. **Single Entry Point**
```
Before: External Service → Service Registry (8090) → [separate service]
After:  External Service → Broker Gateway (8080) → Service Registry (component)
```

All traffic flows through broker-gateway. No bypassing the broker pattern.

#### 3. **Unified Protocol**
External services register using the standard `ServiceRequest` format:

```json
{
  "service": "serviceRegistry",
  "operation": "register",
  "params": {
    "registration": {
      "serviceName": "googleSearchService",
      "operations": ["simpleSearch"],
      "endpoint": "http://localhost:4050",
      "healthCheck": "http://localhost:4050/api/health"
    }
  }
}
```

This is the same protocol used for all other operations.

#### 4. **Simplified Deployment**
- **Before**: 2 services (broker-gateway + service-registry)
- **After**: 1 service (broker-gateway with registry component)

Less infrastructure, fewer ports, simpler Docker Compose.

#### 5. **Cross-Cutting Concerns**
Registration requests now benefit from:
- Authentication/authorization (if added to broker)
- Rate limiting
- Request logging
- Distributed tracing
- Circuit breaking

All handled in one place.

## Implementation Details

### Service Registry Component

Located in `spring/service-registry/`, packaged as a library (not a standalone app):

```java
@Service("serviceRegistry")
public class RegistryService {
    
    @BrokerOperation("register")
    public Map<String, String> register(
        @BrokerParam("registration") ServiceRegistration registration) {
        // Register external service
    }
    
    @BrokerOperation("findByOperation")
    public ServiceRegistration findByOperation(
        @BrokerParam("operation") String operation) {
        // Lookup service by operation name
    }
    
    @BrokerOperation("getAllServices")
    public List<ServiceRegistration> getAllServices() {
        // List all registered services
    }
}
```

### Broker Gateway Integration

The broker-gateway includes service-registry in its component scan:

```java
@ComponentScan(basePackages = {
    "com.angrysurfer.atomic.broker",
    "com.angrysurfer.atomic.registry",  // ← Service registry
    // ... other packages
})
```

### External Service Registration

Moleculer services register via the broker protocol:

```typescript
const serviceRequest = {
  service: "serviceRegistry",
  operation: "register",
  params: {
    registration: {
      serviceName: "googleSearchService",
      operations: ["simpleSearch"],
      endpoint: "http://localhost:4050",
      healthCheck: "http://localhost:4050/api/health"
    }
  }
};

await axios.post('http://localhost:8080/api/broker/submitRequest', serviceRequest);
```

## Benefits for Enterprise Pattern Exploration

This architecture demonstrates:

1. **Service Mesh Concepts**: Centralized routing and service discovery
2. **API Gateway Pattern**: Single entry point for all services
3. **Sidecar Pattern**: External services can register themselves
4. **Polyglot Microservices**: Java, Node.js, future: Python, Go, etc.
5. **Dynamic Service Discovery**: Services register/deregister at runtime

## Comparison to Industry Standards

| Pattern | Our Implementation | Industry Example |
|---------|-------------------|------------------|
| Service Registry | Integrated component | Consul, Eureka |
| API Gateway | Broker Gateway | Kong, Apigee |
| Service Mesh | Broker routing | Istio, Linkerd |
| Protocol | ServiceRequest/Response | gRPC, REST |

## Future Enhancements

1. **Health Checking**: Periodic health checks of registered services
2. **Load Balancing**: Route to multiple instances of same service
3. **Circuit Breaking**: Fail fast when services are down
4. **Service Versioning**: Support multiple versions of same service
5. **Persistent Registry**: Store registrations in MongoDB

## Testing the Registry

### Register a Service
```bash
curl -X POST http://localhost:8080/api/broker/submitRequest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "serviceRegistry",
    "operation": "register",
    "params": {
      "registration": {
        "serviceName": "testService",
        "operations": ["testOp"],
        "endpoint": "http://localhost:5000",
        "healthCheck": "http://localhost:5000/health"
      }
    }
  }'
```

### List All Services
```bash
curl -X POST http://localhost:8080/api/broker/submitRequest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "serviceRegistry",
    "operation": "getAllServices",
    "params": {}
  }'
```

### Find Service by Operation
```bash
curl -X POST http://localhost:8080/api/broker/submitRequest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "serviceRegistry",
    "operation": "findByOperation",
    "params": {
      "operation": "simpleSearch"
    }
  }'
```

## Auto-Registration Pattern

To ensure all services in the monolithic `broker-gateway` are registered, we implemented an **Auto-Registration** mechanism.

### How it Works
1.  **Shared Model**: `ServiceRegistration` is defined in `broker-service-api` for shared access.
2.  **Scanner**: `BrokerAutoRegistration` component in `broker-service` listens for `ApplicationReadyEvent`.
3.  **Detection**: It scans the Spring ApplicationContext for all beans with methods annotated with `@BrokerOperation`.
4.  **Registration**: It groups operations by bean name and sends a `register` request to the `RegistryService` via the `Broker`.

This ensures that as soon as the application starts, all available broker operations are discoverable.

## Refined Architecture: Host-Server as Primary Registry

### The New Pattern

**External services now register directly with host-server**, not broker-gateway:

```
External Service (Moleculer)
    │
    │ 1. Register via REST API
    ▼
┌────────────────┐
│  Host Server   │ ← Primary service registry
│   (Port 8085)  │   Persistent storage (H2)
│   REST API     │   Management plane
└────────┬───────┘
         │
         │ 2. Query for routing (cached)
         ▼
┌────────────────┐
│ Broker Gateway │ ← Queries host-server for service locations
│   (Port 8080)  │   Routes requests (data plane)
└────────┬───────┘
         │
         │ 3. Forward request
         ▼
    Moleculer Service
```

### Why This is Better

1. **Separation of Concerns**
   - **Host Server** (Control Plane): Service registration, catalog, configuration
   - **Broker Gateway** (Data Plane): Request routing, load balancing

2. **Single Source of Truth**
   - Host-server is the authoritative registry
   - Persistent storage survives restarts
   - No sync needed - it IS the registry

3. **Better Admin Experience**
   - Admin console sees services immediately when they register
   - Real-time visibility into service ecosystem

4. **Mirrors Kubernetes Architecture**
   - API Server (host-server) = control plane
   - kube-proxy (broker-gateway) = data plane

## Conclusion

By integrating service-registry into broker-gateway as a `@BrokerOperation` service, we maintain architectural consistency, simplify deployment, and ensure all traffic flows through the broker pattern. This is the right way to implement service discovery in your enterprise sandbox.