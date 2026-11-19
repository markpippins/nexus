# Mock Broker Service

Inherits from: `../ARCHITECTURE.md` (platform) → `../../ARCHITECTURE.md` (root)

## Service

| Setting | Value |
|---------|-------|
| Name | mock-broker-service |
| Framework | Express |
| Port | 8099 (explicit) |
| Node | 18 (override — legacy dependency) |

## Dependencies

| Service | URL | Source |
|---------|-----|--------|
| Broker Gateway | http://localhost:8081 | inherited |

## Overrides

| Setting | Value | Reason |
|---------|-------|--------|
| node.version | 18 | Legacy dependency |
