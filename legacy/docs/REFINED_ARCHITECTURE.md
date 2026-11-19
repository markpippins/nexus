# Refined Architecture: Host-Server as Primary Registry

## Overview

The Atomic Platform now uses a **three-layer architecture** that separates concerns between management (control plane) and runtime (data plane), mirroring enterprise patterns like Kubernetes.

## Architecture Layers

### Layer 1: Management Plane (Host Server)

**Purpose**: Service catalog, configuration management, and administrative operations

**Component**: `host-server` (Port 8085)
- **Database**: H2 (persistent)
- **Responsibilities**:
  - Service registration endpoint
  - Service catalog storage
  - Configuration management
  - Deployment tracking
  - Dependency management
  - Admin API

**Key Endpoints**:
```
POST   /api/registry/register          - Register external service
POST   /api/registry/heartbeat/{name}  - Service heartbeat
GET    /api/registry/services           - List all services
GET    /api/registry/services/by-operation/{op} - Find service by operation
POST   /api/registry/deregister/{name} - Deregister service
```

### Layer 2: Data Plane (Broker Gateway)

**Purpose**: Request routing and service orchestration

**Component**: `broker-gateway` (Port 8080)
- **Responsibilities**:
  - Route client requests
  - Query host-server for service locations
  - Cache service endpoints
  - Load balancing
  - Circuit breaking
  - Request/response transformation

**Flow**:
1. Receive client request
2. Query host-server for service endpoint (with caching)
3. Forward request to target service
4. Return response to client

### Layer 3: Service Layer

**Purpose**: Business logic execution

**Components**:
- **Internal Services** (Spring Boot): user-service, file-service, login-service, etc.
- **External Services** (Polyglot): Moleculer, Python, Go, etc.

## Registration Flow

### External Service Registration

```
┌─────────────────┐
│ Moleculer       │
│ Search Service  │
│  (Port 4050)    │
└────────┬────────┘
         │
         │ 1. POST /api/registry/register
         │    {
         │      serviceName: "googleSearchService",
         │      operations: ["simpleSearch"],
         │      endpoint: "http://localhost:4050",
         │      healthCheck: "/api/health",
         │      framework: "Moleculer",
         │      port: 4050
         │    }
         ▼
┌─────────────────┐
│  Host Server    │
│  (Port 8085)    │
│                 │
│  ┌───────────┐  │
│  │ H2 Database│  │
│  │  Services  │  │
│  │  Configs   │  │
│  └───────────┘  │
└─────────────────┘
```

### Request Routing Flow

```
┌─────────────────┐
│ Angular Client  │
└────────┬────────┘
         │
         │ POST /api/broker/submitRequest
         │ { service: "googleSearchService",
         │   operation: "simpleSearch",
         │   params: { query: "test" } }
         ▼
┌─────────────────┐
│ Broker Gateway  │
│  (Port 8080)    │
└────────┬────────┘
         │
         │ 2. Query: GET /api/registry/services/by-operation/simpleSearch
         ▼
┌─────────────────┐
│  Host Server    │ ← Returns: { endpoint: "http://localhost:4050", ... }
│  (Port 8085)    │
└─────────────────┘
         │
         │ 3. Forward to endpoint
         ▼
┌─────────────────┐
│ Moleculer       │
│ Search Service  │ ← Executes search
│  (Port 4050)    │
└─────────────────┘
```

## Benefits of This Architecture

### 1. Separation of Concerns

| Concern | Host Server | Broker Gateway |
|---------|-------------|----------------|
| Service Registration | ✓ | |
| Service Catalog | ✓ | |
| Configuration Mgmt | ✓ | |
| Request Routing | | ✓ |
| Load Balancing | | ✓ |
| Circuit Breaking | | ✓ |

### 2. Persistent Registry

- **Before**: In-memory registry in broker-gateway (lost on restart)
- **After**: H2 database in host-server (survives restarts)

### 3. Real-Time Admin Visibility

- External services register → immediately visible in admin console
- No manual sync needed
- Live service status tracking

### 4. Scalability

- Host-server can be scaled independently
- Broker-gateway can be scaled independently
- Services can be scaled independently

### 5. Enterprise Pattern Alignment

Mirrors industry-standard architectures:

| Pattern | Our Implementation | Industry Example |
|---------|-------------------|------------------|
| Control Plane | Host Server | Kubernetes API Server |
| Data Plane | Broker Gateway | kube-proxy, Envoy |
| Service Registry | Host Server REST API | Consul, Eureka |
| API Gateway | Broker Gateway | Kong, Apigee |

## Implementation Details

### Host Server: ExternalServiceRegistrationService

```java
@Service
public class ExternalServiceRegistrationService {
    
    @Transactional
    public Service registerExternalService(ExternalServiceRegistration registration) {
        // Create or update service entity
        // Store operations as configurations
        // Store metadata
        // Persist to H2
    }
    
    public Optional<Service> findServiceByOperation(String operation) {
        // Query configurations for operation
        // Return service with endpoint
    }
}
```

### Moleculer: Registry Client

```typescript
async registerWithSpring(): Promise<void> {
  const registrationPayload = {
    serviceName: "googleSearchService",
    operations: ["simpleSearch"],
    endpoint: this.serviceEndpoint,
    healthCheck: `${this.serviceEndpoint}/api/health`,
    framework: "Moleculer",
    version: "1.0.0",
    port: 4050
  };

  await axios.post(`${this.registryUrl}/register`, registrationPayload);
}
```

### Broker Gateway: Service Discovery (Future)

```java
@Service
public class ExternalServiceRouter {
    
    @Value("${host.server.url}")
    private String hostServerUrl;
    
    @Cacheable(value = "serviceEndpoints", key = "#operation")
    public Optional<String> findServiceEndpoint(String operation) {
        // Query host-server
        // Cache result
        // Return endpoint
    }
}
```

## Migration Path

### Phase 1: ✅ Complete
- Host-server with registration API
- Moleculer registers with host-server
- Admin console shows registered services

### Phase 2: In Progress
- Broker-gateway queries host-server for routing
- Implement caching layer
- Add health checking

### Phase 3: Future
- Load balancing across multiple instances
- Circuit breaker integration
- Service versioning support
- Blue/green deployments

## Configuration

### Host Server (application.properties)

```properties
server.port=8085
spring.datasource.url=jdbc:h2:file:./data/hostdb
spring.jpa.hibernate.ddl-auto=update
broker.gateway.url=http://localhost:8080
```

### Moleculer (.env)

```bash
SERVICE_REGISTRY_URL=http://localhost:8085/api/registry
SERVICE_HOST=localhost
SERVICE_PORT=4050
```

### Docker Compose

```yaml
host-server:
  build: ./spring/host-server
  ports:
    - "8085:8085"

moleculer-search:
  build: ./node/moleculer-search
  depends_on:
    - host-server
  environment:
    - SERVICE_REGISTRY_URL=http://host-server:8085/api/registry
```

## Testing

### Register a Service

```bash
curl -X POST http://localhost:8085/api/registry/register \
  -H "Content-Type: application/json" \
  -d '{
    "serviceName": "testService",
    "operations": ["testOp"],
    "endpoint": "http://localhost:5000",
    "healthCheck": "http://localhost:5000/health",
    "framework": "Express",
    "version": "1.0.0",
    "port": 5000
  }'
```

### List Services

```bash
curl http://localhost:8085/api/registry/services
```

### Find Service by Operation

```bash
curl http://localhost:8085/api/registry/services/by-operation/simpleSearch
```

## Conclusion

The refined architecture provides:
- **Clear separation** between control and data planes
- **Persistent registry** that survives restarts
- **Real-time visibility** for administrators
- **Enterprise-grade patterns** that scale
- **Polyglot support** for any framework

This architecture positions the Atomic Platform for production deployment while maintaining the flexibility to explore different microservice patterns.