# Service Health Monitoring and Heartbeat System

## Overview

The Nexus console displays real-time health status of services using a heartbeat-based monitoring system. Services send periodic heartbeats to the host-server registry, which stores them in Redis and exposes them via the `/api/status` endpoint.

## System Architecture

### Backend Components

#### 1. Host Server Registry (`spring/host-server`)
- **Endpoint**: `/api/registry/heartbeat/{serviceName}`
- **Method**: POST
- **Purpose**: Receives heartbeats from services and stores them in Redis
- **TTL**: 60 seconds (services must send heartbeats at least every 60 seconds)

**Key Components**:
- `RegistryController.java` - Heartbeat endpoint (line 67)
- `ServiceStatusCacheService.java` - Redis cache management (line 93)
- `ServiceStatusController.java` - Exposes `/api/status` endpoint (line 62)

#### 2. Service Status Endpoint
- **Endpoint**: `/api/status`
- **Method**: GET
- **Returns**: Array of service statuses with health states
- **Fallback**: Performs live health checks when Redis is unavailable

### Frontend Components

#### 1. ServiceMeshService (`web/angular/nexus`)
- Polls `/api/status` every 10 seconds
- Stores service statuses in `_serviceStatuses` signal
- Falls back to service status when deployments table is empty
- Computes summary with healthy/unhealthy counts

#### 2. Service Tree Sidebar
- Displays services grouped by framework
- Shows health counts per framework group
- Uses service statuses when no deployments exist

## Service Participation Guide

### For New Services

To participate in the health monitoring system, services must:

1. **Register with host-server** (one-time on startup)
   - POST to `/api/registry/register`
   - Include service metadata (name, endpoint, health check URL, etc.)

2. **Send periodic heartbeats** (every 30 seconds recommended)
   - POST to `/api/registry/heartbeat/{serviceName}`
   - Empty body `{}`
   - Timeout: 3 seconds recommended

### Implementation Examples

#### Moleculer Service (TypeScript/Node.js)

**File**: `moleculer/search/services/registry-client.service.ts`

```typescript
async sendHeartbeat(): Promise<void> {
  try {
    const response = await axios.post(
      `${this.registryUrl}/heartbeat/moleculer-search`,
      {},
      {
        headers: { "Content-Type": "application/json" },
        timeout: 3000
      }
    );
    this.logger.debug(`Heartbeat sent successfully`);
  } catch (error: any) {
    this.logger.warn(`Failed to send heartbeat: ${error.message}`);
  }
}
```

**Setup**:
- Initial heartbeat after 2 seconds
- Periodic heartbeat every 30 seconds via `setInterval`
- Graceful cleanup on service stop

#### Helidon Service (Java)

**File**: `helidon/user-access-service/src/.../service/RegistryClientService.java`

```java
@ApplicationScoped
public class RegistryClientService {
    private ScheduledExecutorService scheduler;
    
    @PostConstruct
    public void init() {
        registerService();
        
        scheduler = Executors.newSingleThreadScheduledExecutor();
        scheduler.schedule(this::sendHeartbeat, 2, TimeUnit.SECONDS);
        scheduler.scheduleAtFixedRate(
            this::sendHeartbeat,
            HEARTBEAT_INTERVAL_SECONDS,
            HEARTBEAT_INTERVAL_SECONDS,
            TimeUnit.SECONDS
        );
    }
    
    private void sendHeartbeat() {
        Response response = httpClient
            .target(REGISTRY_URL + "/heartbeat/" + SERVICE_NAME)
            .request(MediaType.APPLICATION_JSON)
            .post(Entity.json("{}"));
    }
}
```

**Setup**:
- Uses CDI `@ApplicationScoped` for auto-initialization
- `@PostConstruct` runs on startup
- `@PreDestroy` handles graceful shutdown

#### Spring Boot Service (Java)

**File**: `spring/*/src/.../service/RegistryClientService.java`

```java
@Service
public class RegistryClientService {
    private final RestTemplate restTemplate;
    
    @Scheduled(fixedRate = 30000, initialDelay = 2000)
    public void sendHeartbeat() {
        try {
            restTemplate.postForObject(
                registryUrl + "/heartbeat/" + serviceName,
                new HashMap<>(),
                Map.class
            );
        } catch (Exception e) {
            logger.warn("Failed to send heartbeat", e);
        }
    }
}
```

**Setup**:
- Use `@Scheduled` annotation
- Enable with `@EnableScheduling` on main class
- Initial delay: 2 seconds
- Fixed rate: 30 seconds

## Configuration

### Environment Variables

All services should support these environment variables:

- `SERVICE_REGISTRY_URL` - Host server registry URL (default: `http://localhost:8085/api/registry`)
- `SERVICE_HOST` - Service hostname (default: `localhost`)
- `SERVICE_PORT` - Service port
- `HEARTBEAT_INTERVAL_MS` - Heartbeat interval in milliseconds (default: `30000`)

### Example `.env` file

```env
SERVICE_REGISTRY_URL=http://host-server:8085/api/registry
SERVICE_HOST=my-service-host
SERVICE_PORT=8080
HEARTBEAT_INTERVAL_MS=30000
```

## Troubleshooting

### Service Not Showing in Console

1. **Check heartbeat logs** - Verify heartbeats are being sent
2. **Check host-server logs** - Confirm heartbeats are being received
3. **Check Redis** - Verify data is being stored
   ```bash
   redis-cli KEYS "service:heartbeat:*"
   redis-cli GET "service:heartbeat:your-service-name"
   ```
4. **Check service name** - Must match exactly between registration and heartbeat
5. **Check timing** - Heartbeats expire after 60 seconds

### Console Shows 0 Healthy Services

If you see this message but services are sending heartbeats:
- Check browser console for `[ServiceMeshService]` logs
- Verify `/api/status` returns data: `http://localhost:8085/api/status`
- Check if deployments table is empty (known issue - status fallback implemented)

### Health Status Not Updating

The frontend polls every 10 seconds. If status doesn't update:
- Clear browser cache
- Check network tab for successful `/api/status` calls
- Verify backend is returning updated timestamps

## Recent Changes (2026-01-20)

### Issue Fixed
Console was showing "0 healthy, 0 unhealthy" despite services sending heartbeats.

### Root Cause
- Deployments table was empty
- Console health counts relied solely on deployment health status
- Service statuses from `/api/status` were being fetched but not used

### Solution
1. **ServiceMeshService** - Added `_serviceStatuses` signal to track live statuses
2. **Summary Computation** - Falls back to service statuses when deployments are empty
3. **Service Tree** - Updated to use service statuses for framework health counts
4. **moleculer-search** - Added heartbeat implementation
5. **user-access-service** - Created RegistryClientService for heartbeats

### Files Modified
- `web/angular/nexus/src/services/service-mesh.service.ts`
- `web/angular/nexus/src/components/service-tree/service-tree.component.ts`
- `moleculer/search/services/registry-client.service.ts`
- `helidon/user-access-service/src/.../service/RegistryClientService.java`

## Best Practices

1. **Heartbeat Interval**: 30 seconds (half of Redis TTL)
2. **Initial Delay**: 2 seconds after startup
3. **Timeout**: 3 seconds for heartbeat requests
4. **Error Handling**: Log warnings but don't crash service
5. **Graceful Shutdown**: Stop heartbeats when service stops
6. **Service Names**: Use consistent naming (e.g., kebab-case)

## Future Enhancements

- [ ] Add deployment record creation for services with heartbeats
- [ ] Implement service-specific health check endpoints
- [ ] Add alerting for services that stop sending heartbeats
- [ ] Create admin UI for viewing heartbeat history
- [ ] Support for service groups/clusters
