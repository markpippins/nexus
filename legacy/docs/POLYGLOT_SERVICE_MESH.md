# Polyglot Service Mesh

## Overview

The Atomic Platform demonstrates a **polyglot service mesh** where services built with different frameworks and languages all register with a central host-server and participate in the same service discovery ecosystem.

## Registered Services

### Spring Boot Services
- **broker-gateway** (Port 8080) - Main gateway
- **user-service** (Port 8083) - User management
- **file-service** (Port 8084) - File operations
- **login-service** (Port 8082) - Authentication

### Quarkus Services
- **broker-gateway** (Port 8090) - Alternative gateway
  - Fast startup (~1 second)
  - Low memory (~50-100 MB)
  - Native compilation capable

### Node.js Services
- **moleculer-search** (Port 4050) - Search service
  - Moleculer microservices framework
  - Google Custom Search integration

### Python Services
- **fs-crawler-adapter** (Port 8001) - File system crawler
  - FastAPI framework
  - Wraps fs-crawler REST API

## Architecture

```
┌──────────────────────────────────────────────────────┐
│              Host Server (Spring Boot)                │
│                   Port 8085                           │
│                                                       │
│  Unified Service Registry:                           │
│  ├── Spring Boot services                            │
│  ├── Quarkus services                                │
│  ├── Node.js services                                │
│  └── Python services                                 │
└──────────────────────────────────────────────────────┘
         ↑           ↑           ↑           ↑
         │           │           │           │
    Spring Boot   Quarkus    Node.js     Python
    (Java)        (Java)     (JS)        (Python)
```

## Registration Pattern

All services follow the same registration pattern:

### 1. Register on Startup
```
POST http://localhost:8085/api/registry/register
{
  "serviceName": "my-service",
  "operations": ["op1", "op2"],
  "endpoint": "http://localhost:PORT",
  "framework": "Framework-Name",
  "port": PORT
}
```

### 2. Send Periodic Heartbeats
```
POST http://localhost:8085/api/registry/heartbeat/my-service
```

### 3. Discoverable by Broker
Broker-gateway queries host-server to route requests.

## Implementation Examples

### Spring Boot (Java)
```java
@Service
public class RegistryService {
    @BrokerOperation("register")
    public Map<String, String> register(@BrokerParam("registration") ServiceRegistration reg) {
        // Register service
    }
}
```

### Quarkus (Java)
```java
@ApplicationScoped
public class HostServerRegistrationService {
    void onStart(@Observes StartupEvent ev) {
        registerService();
    }
}
```

### Node.js (Moleculer)
```javascript
async registerWithSpring() {
  await axios.post(`${HOST_SERVER_URL}/api/registry/register`, {
    serviceName: "moleculer-search",
    operations: ["simpleSearch"],
    endpoint: "http://localhost:4050"
  });
}
```

### Python (FastAPI)
```python
@app.on_event("startup")
async def register_service():
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{HOST_SERVER_URL}/api/registry/register",
            json={
                "serviceName": "fsCrawlerService",
                "operations": ["startScan"],
                "endpoint": "http://localhost:8001"
            }
        )
```

## Benefits

### Framework Flexibility
Choose the best framework for each service:
- **Spring Boot**: Rich ecosystem, enterprise features
- **Quarkus**: Fast startup, low memory, cloud-native
- **Node.js**: Async I/O, JavaScript ecosystem
- **Python**: Data processing, ML, rapid development

### Unified Management
- Single service registry
- Consistent discovery mechanism
- Centralized monitoring
- Unified admin UI

### Technology Diversity
- Mix JVM languages (Java, Kotlin, Scala)
- Mix runtimes (JVM, Node.js, Python, Go)
- Mix frameworks within same language
- Legacy and modern services coexist

## Service Discovery Flow

```
1. Client Request
   ↓
2. Broker Gateway
   ↓
3. Query Host Server: "Which service handles 'startScan'?"
   ↓
4. Host Server Response: "fsCrawlerService at localhost:8001"
   ↓
5. Broker Routes to Service
   ↓
6. Service Executes
```

## Comparison Matrix

| Service | Framework | Language | Startup | Memory | Native |
|---------|-----------|----------|---------|--------|--------|
| broker-gateway | Spring Boot | Java | ~3s | 200MB | No |
| broker-gateway | Quarkus | Java | ~1s | 50MB | Yes |
| moleculer-search | Moleculer | Node.js | ~2s | 80MB | No |
| fs-crawler-adapter | FastAPI | Python | ~1s | 40MB | No |

All participate in the same service mesh!

## Adding New Services

### Any Framework
1. Implement registration on startup
2. Send periodic heartbeats
3. Provide health check endpoint
4. Register operations

### Any Language
- Java: Spring Boot, Quarkus, Micronaut
- JavaScript: Express, NestJS, Moleculer
- Python: FastAPI, Flask, Django
- Go: Gin, Echo
- .NET: ASP.NET Core
- Ruby: Rails, Sinatra

## Real-World Use Cases

### Microservices Migration
- Gradually migrate from monolith
- Mix old (Spring Boot) and new (Quarkus)
- No big-bang rewrite

### Team Autonomy
- Teams choose their frameworks
- Unified service discovery
- Consistent patterns

### Performance Optimization
- Use Quarkus for latency-sensitive services
- Use Node.js for I/O-heavy services
- Use Python for data processing

## See Also

- [Quarkus Integration](../quarkus/broker-gateway/HOST_SERVER_INTEGRATION.md)
- [Moleculer Integration](../node/moleculer-search/README.md)
- [Python Adapter Pattern](REST_API_ADAPTER_PATTERN.md)
- [Host Server Documentation](../spring/host-server/README.md)