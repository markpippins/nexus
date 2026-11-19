# Health Checks and Circuit Breaker Implementation

This document describes the health check endpoints implemented for the three main servers and the circuit breaker logic for monitoring their availability.

## Health Check Endpoints

### 1. Image Server (`node/image-server/image-serv.ts`)

**Endpoint:** `GET /health`
**Port:** 8081 (configurable via `IMAGE_SERVER_PORT`)

**Response Format:**
```json
{
  "status": "UP|DOWN",
  "service": "image-server",
  "timestamp": "2024-11-06T10:30:00.000Z",
  "details": {
    "imageRootDir": "/path/to/images",
    "port": 8081,
    "searchLocations": 5
  }
}
```

**Health Check Logic:**
- Verifies that the image root directory is accessible
- Returns 200 status code when healthy, 503 when unhealthy

### 2. File System Server (`node/file-system-server/fs-serv.ts`)

**Endpoint:** `GET /health`
**Port:** 4040 (configurable via `FS_SERVER_PORT`)

**Response Format:**
```json
{
  "status": "UP|DOWN",
  "service": "file-system-server",
  "timestamp": "2024-11-06T10:30:00.000Z",
  "details": {
    "fsRootDir": "/path/to/fs_root",
    "port": 4040
  }
}
```

**Health Check Logic:**
- Verifies that the file system root directory is accessible
- Returns 200 status code when healthy, 503 when unhealthy

### 3. Broker Gateway (`spring/broker-gateway`)

**Endpoint:** `GET /health`
**Port:** 8080 (default Spring Boot port)

**Response Format:**
```json
{
  "status": "UP|DOWN",
  "service": "broker-gateway",
  "timestamp": "2024-11-06T10:30:00.000Z",
  "details": {
    "application": "BrokerGatewayApplication",
    "version": "1.0.0"
  }
}
```

**Health Check Logic:**
- Basic application health check
- Can be extended to include database connectivity, external service checks, etc.
- Returns 200 status code when healthy, 503 when unhealthy

## Circuit Breaker Implementation

The circuit breaker implementation is located in `node/utils/circuit-breaker.ts` and provides:

### Circuit States
- **CLOSED**: Normal operation, requests are allowed
- **OPEN**: Service is considered down, requests are blocked
- **HALF_OPEN**: Testing if service has recovered

### Configuration
```typescript
const CIRCUIT_BREAKER_CONFIG = {
  failureThreshold: 3,      // Open circuit after 3 consecutive failures
  resetTimeout: 30000,      // Wait 30 seconds before attempting to close circuit
  monitoringPeriod: 10000   // Check health every 10 seconds
};
```

### Usage

#### Programmatic Usage
```typescript
import { HealthMonitor } from './node/utils/circuit-breaker';

const monitor = new HealthMonitor();

// Check all services
const results = await monitor.checkAllServices();

// Check specific service
const imageServerStatus = await monitor.checkService('image-server');
```

#### CLI Usage
```bash
# Run the health monitor
cd node/utils
ts-node circuit-breaker.ts

# Or if compiled to JavaScript
node circuit-breaker.js
```

### Example Output
```
=== Health Check Results ===
image-server: ✅ UP [Circuit: CLOSED]
file-system-server: ❌ DOWN [Circuit: OPEN]
  Error: Circuit breaker OPEN for file-system-server. Next attempt in 25s
broker-gateway: ✅ UP [Circuit: CLOSED]
```

## Testing the Health Checks

### Manual Testing
```bash
# Test image server health
curl http://localhost:9081/health

# Test file system server health
curl http://localhost:4040/health

# Test broker gateway health
curl http://localhost:8080/health
```

### Integration with Load Balancers
These health check endpoints can be used with:
- Nginx upstream health checks
- HAProxy health checks
- Kubernetes liveness/readiness probes
- AWS Application Load Balancer health checks

### Example Nginx Configuration
```nginx
upstream image_servers {
    server localhost:9081 max_fails=3 fail_timeout=30s;
    # Add more servers as needed
}

server {
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
    
    location / {
        proxy_pass http://image_servers;
        # Health check will be performed automatically by Nginx
    }
}
```

## Extending the Health Checks

### Adding Database Connectivity Check (Spring Boot)
```java
@Autowired
private MongoTemplate mongoTemplate;

@GetMapping("/health")
public ResponseEntity<Map<String, Object>> health() {
    // ... existing code ...
    
    try {
        // Test database connectivity
        mongoTemplate.getCollection("test").countDocuments();
        details.put("database", "UP");
    } catch (Exception e) {
        details.put("database", "DOWN");
        healthStatus.put("status", "DOWN");
        return ResponseEntity.status(503).body(healthStatus);
    }
    
    // ... rest of method ...
}
```

### Adding External Service Dependency Check (Node.js)
```typescript
// In the health check handler
try {
    // Check external service dependency
    const externalServiceResponse = await fetch('http://external-service/health');
    if (!externalServiceResponse.ok) {
        throw new Error('External service unavailable');
    }
    
    // ... rest of health check logic ...
} catch (error) {
    // Handle external service failure
}
```