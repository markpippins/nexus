# Architecture Refinement Summary

## What Changed

External services now register with **host-server** (port 8085) instead of broker-gateway (port 8080).

### Before
```
Moleculer → Broker Gateway → In-memory Registry
```

### After
```
Moleculer → Host Server → Persistent Registry (H2)
                ↑
                │ (queries)
          Broker Gateway
```

## Key Changes

1. **Host Server**: Added REST API for service registration
2. **Moleculer**: Changed registration URL from 8080 to 8085
3. **Architecture**: Separated control plane (host-server) from data plane (broker-gateway)

## Benefits

- ✅ Persistent registry (survives restarts)
- ✅ Real-time admin visibility
- ✅ Clear separation of concerns
- ✅ Mirrors Kubernetes architecture

## Files Created

- `spring/host-server/src/main/java/com/angrysurfer/atomic/hostserver/controller/RegistryController.java`
- `spring/host-server/src/main/java/com/angrysurfer/atomic/hostserver/service/ExternalServiceRegistrationService.java`
- `spring/host-server/src/main/java/com/angrysurfer/atomic/hostserver/dto/ExternalServiceRegistration.java`
- `spring/host-server/Dockerfile`
- `docs/REFINED_ARCHITECTURE.md`

## Files Modified

- `node/moleculer-search/.env` - Changed registry URL
- `node/moleculer-search/services/registry-client.service.ts` - Direct REST API
- `README.md` - Updated architecture diagram
- `docker-compose.yml` - Added host-server dependency

## Testing

```bash
# Start host-server
cd spring/host-server && ./mvnw spring-boot:run

# Start moleculer
cd node/moleculer-search && npm run dev

# Verify registration
curl http://localhost:8085/api/registry/services
```