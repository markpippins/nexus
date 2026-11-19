# Nexus Console - Service Health Display Fix

**Date**: 2026-01-20  
**Status**: ✅ Resolved

## Problem

The Nexus console was displaying "0 healthy, 0 unhealthy" services despite:
- Services successfully sending heartbeats to host-server
- Backend `/api/status` endpoint returning 3 healthy services
- Frontend successfully fetching the status data every 10 seconds

## Investigation

### Initial Findings

Console logs showed:
```
[ServiceMeshService] Fetched deployments: 0
[ServiceMeshService] Fetched live statuses: 3
[ServiceMeshService] Service names from /api/status: 
  ["quarkus-broker-gateway", "spring-broker-gateway", "moleculer-search"]
```

### Root Cause

1. **Deployments table was empty** (0 records)
2. **Health calculation relied on deployments**:
   ```typescript
   healthyDeployments = deployments.filter(d => d.healthStatus === 'HEALTHY').length;
   ```
3. **Service statuses were fetched but not used** when deployments were empty
4. **Both summary cards AND sidebar tree** had the same issue

## Solution

### 1. ServiceMeshService - Summary Fallback

**File**: `web/angular/nexus/src/services/service-mesh.service.ts`

Added fallback logic to use service statuses when deployments are empty:

```typescript
// If we have deployments, use them
if (deployments.length > 0) {
  healthyDeployments = deployments.filter(d => d.healthStatus === 'HEALTHY').length;
  unhealthyDeployments = deployments.filter(d => d.healthStatus === 'UNHEALTHY').length;
} 
// Otherwise, use service statuses from /api/status
else if (serviceStatuses.size > 0) {
  const statusArray = Array.from(serviceStatuses.values());
  healthyDeployments = statusArray.filter(s => s.healthStatus === 'HEALTHY').length;
  unhealthyDeployments = statusArray.filter(s => s.healthStatus === 'UNHEALTHY').length;
}
```

**Changes**:
- Line 52: Added `_serviceStatuses` signal to track live statuses
- Lines 90-128: Updated summary computation with fallback logic
- Lines 295-327: Modified `fetchAllData()` to store service statuses
- Lines 440-475: Enhanced logging to debug status mapping

### 2. Service Tree - Sidebar Health Counts

**File**: `web/angular/nexus/src/components/service-tree/service-tree.component.ts`

Updated framework group health calculations to use service statuses:

```typescript
// If we have deployments, use them
if (frameworkDeployments.length > 0) {
  healthy = frameworkDeployments.filter(d => d.healthStatus === 'HEALTHY').length;
  // ...
}
// Otherwise, use service statuses from /api/status
else if (serviceStatuses.size > 0) {
  frameworkServices.forEach(service => {
    const status = serviceStatuses.get(service.name);
    if (status?.healthStatus === 'HEALTHY') healthy++;
    else if (status?.healthStatus === 'UNHEALTHY') unhealthy++;
    else unknown++;
  });
}
```

**Changes**:
- Line 51: Access service statuses from ServiceMeshService
- Lines 81-110: Added fallback logic for framework health counts

### 3. Service Participation

Added heartbeat implementations for services:

#### moleculer-search
**File**: `moleculer/search/services/registry-client.service.ts`
- Added `heartbeatInterval` property
- Added `sendHeartbeat()` method
- Sends POST to `/api/registry/heartbeat/moleculer-search` every 30 seconds

#### user-access-service
**File**: `helidon/user-access-service/src/.../service/RegistryClientService.java`
- Created new `@ApplicationScoped` CDI service
- Uses JAX-RS client for heartbeats
- Sends POST to `/api/registry/heartbeat/user-access-service` every 30 seconds

## Verification

### Before Fix
```
Console Summary Cards: 0 healthy, 0 unhealthy
Sidebar Tree: 0 healthy, 0 unhealthy
Console Logs: "Fetched live statuses: 3"
```

### After Fix
```
Console Summary Cards: 3 healthy, 0 unhealthy ✓
Sidebar Tree: 3 healthy, 0 unhealthy (by framework) ✓
Console Logs: "FINAL SUMMARY VALUES: healthyDeployments: 3" ✓
```

### Active Services
1. **quarkus-broker-gateway** - HEALTHY
2. **spring-broker-gateway** - HEALTHY
3. **moleculer-search** - HEALTHY

## Testing

### Manual Testing Steps

1. **Verify backend is running**:
   ```bash
   curl http://localhost:8085/api/status
   ```
   Should return array with service statuses.

2. **Check browser console**:
   ```
   [ServiceMeshService] Fetched live statuses: 3
   [ServiceMeshService] healthyDeployments count: 3
   [ServiceMeshComponent] Healthy deployments: 3
   ```

3. **Verify UI displays**:
   - Summary cards show correct counts
   - Sidebar tree shows counts by framework
   - Individual services show green health indicators

### Automated Testing

None implemented (UI-heavy feature).

## Future Improvements

1. **Create deployment records** for services with active heartbeats
2. **Direct service health view** independent of deployments
3. **Real-time WebSocket updates** instead of 10-second polling
4. **Health history** to track service uptime over time
5. **Alerting** when services stop sending heartbeats

## Related Files

### Modified
- `web/angular/nexus/src/services/service-mesh.service.ts`
- `web/angular/nexus/src/components/service-tree/service-tree.component.ts`
- `web/angular/nexus/src/components/service-mesh/service-mesh.component.ts`
- `moleculer/search/services/registry-client.service.ts`

### Created
- `helidon/user-access-service/src/.../service/RegistryClientService.java`

### Backend (Reference)
- `spring/host-server/src/.../controller/RegistryController.java`
- `spring/host-server/src/.../controller/ServiceStatusController.java`
- `spring/host-server/src/.../service/ServiceStatusCacheService.java`

## Lessons Learned

1. **Fallback mechanisms are essential** when data sources might be empty
2. **Debug logging** was critical for identifying the issue
3. **Component isolation** - Summary and sidebar had separate implementations
4. **Service naming consistency** - Must match across registration and heartbeats
5. **Data flow tracing** - Following data from backend → service → component → template
