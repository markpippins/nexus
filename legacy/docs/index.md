# Atomic Project Documentation Index

Welcome to the documentation index for the Atomic project. This file provides organized access to all documentation across the various subprojects.

## Core Documentation

- [README](README.md) - Main project overview
- [Projects Overview](projects.md) - List and description of all projects
- [License](LICENSE) - Project licensing information

## Main Documentation Files

### Architecture & Patterns
- [User Service Architecture](docs/user-service-architecture.md) - Architecture overview of the user service
- [Nucleus Broker Service](docs/nucleus-broker-service.md) - Documentation for the nucleus broker service
- [Nucleus Broker Service Login](docs/nucleus-broker-service-login.md) - Login specific documentation for the broker service
- [Service Registry Architecture](docs/SERVICE_REGISTRY_ARCHITECTURE.md) - Service discovery and registration patterns
- [Refined Architecture](docs/REFINED_ARCHITECTURE.md) - Host-server as primary registry
- [REST API Adapter Pattern](docs/REST_API_ADAPTER_PATTERN.md) - **How to wrap any REST API for broker integration**
- [Service Backend Implementation](docs/SERVICE_BACKEND_IMPLEMENTATION.md) - Service instance backend connections
- [FS Crawler Adapter Implementation](docs/FS_CRAWLER_ADAPTER_IMPLEMENTATION.md) - REST API adapter example
- [Polyglot Service Mesh](docs/POLYGLOT_SERVICE_MESH.md) - **Multi-framework service registration** (Spring Boot, Quarkus, Node.js, Python)

## Web Projects

### Next.js Applications
- [Next.js Cool People](web/nextjs-cool-people/README.md) - Social application for connecting with interesting people
- [Next.js Cool People Documentation](web/nextjs-cool-people/docs/blueprint.md) - Technical blueprint for cool people app
- [Next.js API Tester](web/nextjs-api-tester/README.md) - API testing utility
- [Next.js API Tester Documentation](web/nextjs-api-tester/docs/blueprint.md) - Technical blueprint for API tester

### Angular Applications
- [Angular Atomic Admin](web/angular-atomic-admin/README.md) - Admin dashboard for managing Servers and Services
- [Angular Cool People Admin](web/angular-cool-people-admin/README.md) - Admin dashboard for Cool People
- [Admin Backend Requirements](web/angular-cool-people-admin/backend-requirements.md) - Backend requirements for Admin and Next.js apps
- [Angular Nexus](web/angular/nexus/README.md) - Angular nexus application
- [PowerShell Web Tool](web/pwsh/README.md) - PowerShell utilities for web
- [Bash Web Utilities](web/bash/README.md) - Bash utilities for web
- 
- ## Multi-Language SDKs
- [Broker SDK Implementation Complete](atomic/CLIENT_LIBRARY_IMPLEMENTATION.md) - **Lightweight client libraries for Python, Node.js, and Go services** to interact with the broker gateway
- [Broker SDK Overview](atomic/BROKER_SDK_README.md) - Complete SDK documentation and usage examples

### Web POC (Proof of Concept)
- [Next.js Log Watch POC](web-poc/nextjs-log-watch/README.md) - Real-time log monitoring application proof of concept
- [Next.js Log Watch Documentation](web-poc/nextjs-log-watch/docs/blueprint.md) - Technical blueprint for log watch
- [Google Custom Search App POC](web-poc/google-custom-search-app/README.md) - Custom search implementation
- [Google Cloud Search Assistant POC](web-poc/google-cloud-search-assistant/README.md) - Assistant for Google Cloud Search
- [React Cripto API Tester POC](web-poc/react-cripto-api-tester/README.md) - Cryptocurrency API testing tool
- [React App POC](web-poc/react-app/README.md) - React application proof of concept

## Node.js Projects

- [Node.js README](node/README.md) - Overview of Node.js projects
- [Broker Service Proxy](node/broker-service-proxy/README.md) - Proxy service for broker communications
- [Moleculer Search Service](node/moleculer-search/README.md) - Moleculer-based search service with Google integration

## Go Projects

- [Go README](go/README.md) - Overview of Go projects

## Helidon Projects

- [Helidon README](helidon/README.md) - Overview of Helidon projects

## Python Projects

- [Python README](python/README.md) - Overview of Python projects
- [FS Crawler](python/fs-crawler/README.md) - File system crawler and media metadata service
- [FS Crawler Adapter](python/fs-crawler-adapter/README.md) - **Broker adapter example** - Demonstrates wrapping REST APIs for broker integration

## Quarkus Projects

- [Broker Gateway Quarkus](quarkus/broker-gateway/README.md) - Quarkus implementation of broker gateway
- [Host Server Integration](quarkus/broker-gateway/HOST_SERVER_INTEGRATION.md) - Polyglot service registration example

## Spring Boot Projects

### Core Services
- [Spring README](spring/README.md) - Overview of Spring projects
- [User Service](spring/user-service/README.md) - User management service
- [User API](spring/user-api/README.md) - API for user management
- [User Access Service](spring/user-access-service/README.md) - Access control service
- [Login Service](spring/login-service/README.md) - Authentication service
- [Note Service](spring/note-service/README.md) - Notes management service with MongoDB persistence and token-based authentication
- [Upload Service](spring/upload-service/README.md) - File upload service
- [Export Service](spring/export-service/README.md) - Data export service
- [File Service](spring/file-service/README.md) - File management service
- [File Service API](spring/file-service-api/README.md) - API for file service

### Authentication & Security Documentation
- [Token-Based Authentication Integration](docs/token-auth-integration.md) - Documentation for token-based authentication between file-service and login-service
- [User Authentication Flow](docs/user-auth-flow.md) - Complete user authentication flow documentation
- [Broker Security Patterns](docs/broker-security.md) - Security patterns for broker-based services

### Broker Services
- [Broker Service](spring/broker-service/README.md) - Main broker service
- [Broker Service API](spring/broker-service-api/README.md) - API for broker service
- [Broker Service SPI](spring/broker-service-spi/README.md) - Service Provider Interface for broker service
- [Broker Gateway](spring/broker-gateway/README.md) - Gateway for broker communications
- [Broker Gateway Security Bot](spring/broker-gateway-sec-bot/README.md) - Security bot for gateway
- [Broker Gateway Security Bot Additional](spring/broker-gateway-sec-bot/README-2.md) - Additional security bot documentation

### Data Services
- [Shrapnel Data](spring/shrapnel-data/README.md) - Data management service

### Management & Operations
- [Host Server](spring/host-server/README.md) - Comprehensive server/service/configuration management system
- [API Examples](spring/host-server/API_EXAMPLES.md) - Practical API usage examples with curl and PowerShell
- [Architecture](spring/host-server/ARCHITECTURE.md) - Detailed architecture and design patterns
- [Diagrams](spring/host-server/DIAGRAMS.md) - Visual representations of data model and flows
- [Quick Reference](spring/host-server/QUICK_REFERENCE.md) - Fast lookup guide for common operations
- [Implementation Summary](spring/host-server/IMPLEMENTATION_SUMMARY.md) - Overview of what was built and why
- [Production Guide](spring/host-server/PRODUCTION_GUIDE.md) - Migration path to production deployment
- [Backend Connections API](spring/host-server/BACKEND_CONNECTIONS_API.md) - Service instance backend connections
- [Backend Quick Start](spring/host-server/BACKEND_QUICK_START.md) - Quick reference for backend connections

## Utility Projects

### Scripts
- [Scripts Folder](scripts/) - Various utility scripts

## Other Resources

### Environment Setup
- [Docker Compose](docker-compose.yml) - Docker configuration for the project
- [Makefile](Makefile) - Build automation commands
- [Procfile](Procfile) - Process type declarations for deployment

## Additional Documentation

### Core Guides
- [Getting Started with Search](docs/GETTING_STARTED_SEARCH.md)
- [Service Registry Architecture](docs/SERVICE_REGISTRY_ARCHITECTURE.md)
- [Angular Nexus Setup](docs/nexus-setup.md)
- [CORS Troubleshooting](docs/cors-troubleshooting.md)
- [Environment Management](docs/env-management.md)
- [Health Checks](docs/health-checks.md)
- [Spring Profiles Guide](docs/spring-profiles-guide.md)
