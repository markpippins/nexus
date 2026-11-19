# Nucleus Service Broker System

A comprehensive microservices architecture system that provides a unified service broker for various backend services including user management, file operations, and data processing.

## Overview

The Nucleus Service Broker System is a modular, scalable platform designed to route client requests to appropriate backend services. It consists of multiple interconnected services that work together to provide a unified interface for various business operations.

## Architecture

The system is organized as a multi-module Maven project with the following key components:

### Core Services
- **broker-gateway**: The main gateway that routes requests to appropriate services
- **broker-service**: Core service brokering logic
- **broker-service-api**: API definitions for service communication
- **broker-service-spi**: Service Provider Interface for extensibility

### Business Services
- **user-service**: User management and authentication
- **user-api**: User service APIs
- **file-service**: File system operations
- **file-service-api**: File service APIs
- **export-service**: Data export functionality
- **upload-service**: File upload handling
- **login-service**: Authentication service

### Specialized Components
- **vaadin-nucleus-client**: Vaadin-based web client application
- **sec-bot**: Security bot functionality
- **shrapnel-data**: Data processing component

## Building the Project

To build the entire project, run:

```bash
mvn clean install
```

Each service can be built independently or as part of the multi-module build.

## Running the System

The system can be run as a collection of microservices. Each service typically runs on its own port, with the broker-gateway serving as the main entry point for client requests.

## Configuration

Configuration is typically handled through application.properties files in each service, with environment-specific overrides possible through external configuration.

## Contributing

Contributions are welcome. Please ensure proper documentation and testing of new features.