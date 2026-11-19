# Atomic Platform

A comprehensive polyglot microservices platform featuring broker-based service architecture, service mesh management, and distributed service discovery.

## Overview

The Atomic platform is a production-ready distributed system supporting multiple programming languages and frameworks. Built on **Spring Boot 3.5.0** with Java 21, it provides:

- **✅ Service Mesh Management**: Real-time service discovery and visualization via Nexus UI
- **✅ Broker Gateway**: Central hub for request routing and service orchestration  
- **✅ Host-Server Registry**: Persistent service registry with MySQL/H2 storage
- **✅ Polyglot SDKs**: Production-ready client libraries (Python, Node.js, Go)
- **✅ Broker Gateway Proxy**: Advanced reverse proxy with rate limiting and logging
- **✅ User Management**: Dual-architecture user services with MongoDB persistence
- **✅ External Service Integration**: Seamless integration of services across frameworks

## Architecture

### **Three-Layer Service Mesh Architecture**

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

### **Core Components**

#### 1. **Host-Server** (Port 8085) - ✅ Production Ready
**Service Registry & Management**
- Persistent service registry (MySQL/H2)
- Framework management (Spring Boot, Quarkus, Helidon, Node.js, Go, Python)
- Service discovery via operation-based lookup
- Deployment tracking across servers
- Configuration management per environment
- Real-time service health monitoring

#### 2. **Broker Gateway** (Port 8081) - ✅ Production Ready  
**Request Routing & Service Orchestration**
- ServiceRequest/ServiceResponse protocol
- Automatic service discovery and routing
- External service proxy with fallback mechanisms
- Health check aggregation
- Load balancing and circuit breaker patterns

#### 3. **Broker Gateway Proxy** (Port 8080) - ✅ Production Ready
**Advanced Reverse Proxy (AdonisJS)**
- Public-facing entry point for all client requests
- Rate limiting and request logging
- Auto-registration with host-server
- Heartbeat mechanism (30-second intervals)
- Graceful shutdown with deregistration
- Request context headers for tracing

#### 4. **Nexus Service Mesh UI** - ✅ Production Ready
**Real-time Service Management Console**
- Service mesh visualization (default view)
- Framework-grouped service listing
- Dependency graph visualization
- Service operations (restart, logs, health checks)
- Real-time status updates
- Dual-pane interface for resource comparison

### **Service Discovery Flow**

```
1. Service Registration → Host-Server (/api/registry/register)
2. Client Request → Broker Proxy (Port 8080)
3. Service Discovery → Host-Server (operation-based lookup)
4. Request Routing → Broker Gateway → Target Service
5. Response Chain → Target Service → Broker Gateway → Proxy → Client
6. Health Monitoring → Continuous heartbeat and status updates
```

## 📊 Platform Status & Evolution

### Current Platform Maturity

The Atomic Platform has evolved from a file-explorer-based tool to a **comprehensive service mesh management platform** with advanced discovery, monitoring, and operational capabilities.

### Recent Major Achievements

#### **Application Separation** ✅ **COMPLETED**
- **Nexus**: Service Mesh Management Console (default view)
- **Throttler**: Search & Discovery Engine
- Clean separation of concerns between service management and search capabilities

#### **Service Discovery System** ✅ **PRODUCTION READY**
- **ServiceDiscoveryClient**: Host-server integration for service lookup
- **ExternalServiceInvoker**: Dynamic external service invocation  
- **BrokerAutoRegistration**: Annotation-based service exposure
- **Fallback Mechanism**: Local → External service resolution
- **Unified Interface**: Same API for local and external services

#### **Polyglot Service Integration** ✅ **FULLY OPERATIONAL**
Support for multiple programming languages and frameworks:
- **Java**: Spring Boot 3.5.0, Quarkus 3.15.1, Helidon MP
- **Node.js**: Express, NestJS, AdonisJS, Moleculer
- **Python**: FastAPI, Django, Flask
- **Go**: Standard library, Gin framework
- **Operation-based routing** across all frameworks

#### **Service Mesh UI** ✅ **DEFAULT NEXUS VIEW**
- **ServiceMeshComponent**: Full service mesh visualization
- **ServiceTreeComponent**: Framework-grouped service listing
- **ServiceGraphComponent**: Dependency visualization
- **Real-time Updates**: Polling and reactive state management
- **Service Operations**: Restart, logs, health checks

## ✅ Production-Ready Features

### **Polyglot SDK Support**
**Complete client libraries for multiple languages:**

- **Python SDK** (`python/broker-client/`) - Service discovery, operation invocation, health checking
- **Node.js SDK** (`node/broker-client/`) - Full feature parity with Python SDK  
- **Go SDK** (`go/broker-client/`) - Standard library implementation, no external dependencies

**Usage Example (Python)**:
```python
from atomic_broker_sdk import create_client, ServiceDetails

client = create_client(gateway_url="http://localhost:8080")
response = client.invoke_operation("getUserRegistrationForToken", {"token": "sample-token"})
```

### **Supported Frameworks**
**Production-ready integrations:**

- **Java**: Spring Boot 3.5.0, Quarkus 3.15.1, Helidon MP
- **Node.js**: Express, NestJS, AdonisJS, Moleculer  
- **Python**: FastAPI, Django, Flask
- **Go**: Standard library, Gin framework
- **Others**: .NET Core, Rust (planned)

### **Service Registration & Discovery**
**Automatic service integration:**

```bash
# Services auto-register on startup
POST /api/registry/register
{
  "serviceName": "my-microservice",
  "endpoint": "http://localhost:3001", 
  "framework": "FastAPI",
  "operations": ["getUserData", "updateProfile"],
  "metadata": { "version": "1.0.0" }
}
```

### **Real-time Service Mesh**
**Nexus UI provides:**
- Live service status monitoring
- Dependency graph visualization
- Service operations (restart, logs, health)
- Framework-grouped service listing
- Performance metrics and alerts
3. **Token Validation**: Other services (like file-service, note-service) validate tokens with login-service via broker communication
4. **Access Control**: Token validation ensures users can only access their own resources
5. **Service Decoupling**: All inter-service communication now occurs through the broker service to improve modularity and maintainability

#### Current Token-Based Integrations

- **file-service**: Requires tokens for all file operations instead of direct alias access, communicates with login-service via broker
- **note-service**: Validates user tokens for note operations through the broker
- **login-service**: Formerly had direct dependency on user-access-service, now uses broker service for user validation (refactored for better decoupling)
- **Cross-Service Security**: All file and note operations validated against user's authenticated identity
- **Session Management**: Tokens can be invalidated on logout

#### Broker Service Integration Pattern

Many services now communicate internally through the broker service rather than direct HTTP calls:
- Services register with the broker and use ServiceRequest/ServiceResponse patterns for communication
- This provides better service discovery, load balancing, and fault tolerance
- The broker acts as a central orchestration point for all internal service communications

#### Security Benefits

- **User Isolation**: Users can only access their own files/folders/notes
- **Reduced Attack Surface**: No direct alias exposure in operations
- **Centralized Validation**: All authentication handled by login-service via broker
- **Audit Trail**: All operations tied to authenticated sessions

## Service Architecture

### Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Angular Admin Console (9000)                │
│                  (Management UI)                         │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
            ┌────────────────┐
            │  Host Server   │ ← Control Plane (Port 8085)
            │   (H2 DB)      │   - Service registry
            └────────┬───────┘   - Service catalog
                     │           - Configuration mgmt
                     │ query
                     ▼
            ┌────────────────┐
            │ Broker Gateway │ ← Data Plane (Port 8080)
            │                │   - Request routing
            └────────┬───────┘   - Load balancing
                     │
          ┌──────────┴──────────┐
          │                     │
    Internal Services    External Services
    ├── File System      ├── Moleculer Search (4050)
    ├── User Access      └── Future services...
    ├── Login
    └── User Service
```

### External Service Registration

The platform supports **dynamic service registration** for polyglot microservices:

1. **Host Server**: Primary service registry with persistent storage (H2)
2. **Registration**: External services register via REST API at `POST /api/registry/register`
3. **Discovery**: Broker-gateway queries host-server to route requests
4. **Admin UI**: Real-time visibility into registered services

**Registration Flow**:
```
Moleculer Service → Host Server (register) → Persistent DB
Broker Gateway → Host Server (query) → Route to service
```

This architecture separates concerns: host-server manages the service catalog (control plane), while broker-gateway handles request routing (data plane).

## Running the Platform

### Quick Start with Docker Compose (Recommended)

Start the entire platform with one command:

```bash
docker-compose up --build
```

This will start all services with proper networking and dependencies.

### Service Ports and URLs

#### Databases

- **MySQL**: `localhost:3306` (user: root, password: rootpass)
- **MongoDB**: `localhost:27017`
- **Redis**: `localhost:6379` (for session management and caching)

#### Spring Boot Services

- **host-server**: `http://localhost:8085` - Service registry and management (H2)
- **broker-gateway**: `http://localhost:8080` - Main API gateway and request router
- **user-access-service**: `http://localhost:8081` - Legacy-compatible user management (MySQL)
- **login-service**: `http://localhost:8082` - Authentication service
- **user-service**: `http://localhost:8083` - Primary user management (MongoDB)

#### Node.js Services

- **file-system-server**: `http://localhost:4040` - Proxy file system service
- **moleculer-search**: `http://localhost:4050` - Moleculer-based search service (Google, Gemini, Unsplash)

#### Web Applications

- **nextjs-api-tester**: `http://localhost:9012` - API testing interface
- **nextjs-cool-people**: `http://localhost:9002` - User interface application

#### Angular Applications

- **atomic-admin**: `http://localhost:4200` - Atomic admin console
- **cool-people-admin**: `http://localhost:4201` - Cool people admin console
- **nexus**: `http://localhost:3001` - Nexus application
- **throttler**: `http://localhost:9007` - Throttler application
- **web-poc host-server-admin**: `http://localhost:3002` - Host server admin console
- **web-poc projman-ui**: `http://localhost:3029` - Project management UI
- **web-poc throttler-alt**: `http://localhost:3003` - Alternative throttler application
- **web-poc throttler-og**: `http://localhost:9008` - Original throttler application

#### React Applications

- **web host-server-admin**: `http://localhost:5173` - Host server admin (Vite)
- **web-poc cripto-api-tester**: `http://localhost:5174` - Crypto API tester (Vite)
- **web-poc google-cloud-search-assistant**: `http://localhost:5175` - Google Cloud search assistant (Vite)
- **web-poc google-custom-search-app**: `http://localhost:5176` - Google custom search app (Vite)

### Manual Database Setup

If not using Docker Compose, start databases manually:

```bash
# MySQL
./start-mysql.sh

# MongoDB (Windows)
mongodb-docker-start.bat

# MongoDB (Linux/Mac)
./mongodb-docker-start.sh

# Redis (for session management)
./start-redis.sh  # Linux/Mac
./start-redis.ps1  # Windows PowerShell
```

### Manual Service Startup

For development, services can be started individually:

```bash
# Spring Boot services (from respective directories)
cd spring/broker-gateway && ./mvnw spring-boot:run
cd spring/user-access-service && ./mvnw spring-boot:run -Dspring-boot.run.arguments="--server.port=8081"
cd spring/login-service && ./mvnw spring-boot:run -Dspring-boot.run.arguments="--server.port=8082"
cd spring/user-service && ./mvnw spring-boot:run -Dspring-boot.run.arguments="--server.port=8083"

# Quarkus service
cd quarkus/broker-gateway && ./mvnw compile quarkus:dev

# Node.js services
cd node/file-system-server && npm start

# Web applications
cd web/nextjs-api-tester && npm run dev
cd web/nextjs-cool-people && npm run dev
```

### Alternative Orchestration

Use the provided orchestration tools:

```bash
# Using Foreman (requires: npm install)
npm run dev

# Using Make
make dev

# Using individual commands
make start-db
make start-services
make start-web
```

## Development

The platform is designed for modular development with clear separation of concerns between services.

### Service Dependencies

```
Web Apps (9002, 9012)
    ↓
Broker Gateway (8080)
    ↓
├── File System Server (4040)
├── User Access Service (8081) → MySQL
├── Login Service (8082) → User Access Service
└── User Service (8083) → MongoDB
```

### Development Workflow

1. **Start databases**: `docker-compose up mysql mongodb -d`
2. **Start core services**: `docker-compose up broker-gateway user-access-service user-service -d`
3. **Start web apps**: `docker-compose up nextjs-api-tester nextjs-cool-people -d`

Or start everything at once: `docker-compose up --build`

### Configuration Files

- **Docker Compose**: `docker-compose.yml` - Complete orchestration
- **Process Manager**: `Procfile` - For Foreman-based development
- **Make**: `Makefile` - Granular control over services
- **Package.json**: Root-level scripts for common tasks
