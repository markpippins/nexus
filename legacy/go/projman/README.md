# Projman Service v2.0

A comprehensive project management service built in Go that registers with Atomic Platform's host-server for service discovery. Now supports hierarchical project structure with MySQL database.

## Overview

Projman is a microservice that allows you to manage projects with hierarchical structure:

- **Projects**: Top-level containers for organizing work
- **Subsystems**: Components within projects that group related features
- **Features**: Specific functionality within subsystems
- **Requirements**: Detailed requirements that can be attached to projects, subsystems, or features
- **Sub-items**: Granular tasks within requirements
- Track status of all entities (pending, in-progress, complete)

## API Endpoints

### Projects Management
- `GET /projects` - List all projects
- `GET /projects/{id}` - Get a specific project with subsystems
- `POST /projects` - Create a new project
- `PUT /projects/{id}` - Update a project
- `DELETE /projects/{id}` - Delete a project
- `GET /projects/{id}/subsystems` - Get subsystems for a project

### Subsystems Management
- `GET /subsystems` - List all subsystems with project info
- `GET /subsystems/{id}` - Get a specific subsystem with features
- `POST /subsystems` - Create a new subsystem
- `PUT /subsystems/{id}` - Update a subsystem
- `DELETE /subsystems/{id}` - Delete a subsystem
- `GET /subsystems/{id}/features` - Get features for a subsystem

### Features Management
- `GET /features` - List all features with subsystem info
- `GET /features/{id}` - Get a specific feature with requirements
- `POST /features` - Create a new feature
- `PUT /features/{id}` - Update a feature
- `DELETE /features/{id}` - Delete a feature

### Requirements Management
- `GET /requirements` - List all requirements with full relationships
- `GET /requirements/{id}` - Get a specific requirement with project/subsystem/feature
- `POST /requirements` - Create a new requirement
- `PUT /requirements/{id}` - Update a requirement
- `DELETE /requirements/{id}` - Delete a requirement
- `GET /requirements/status/{status}` - Get requirements filtered by status

### Sub-items Management
- `POST /requirements/{id}/subitems` - Add a sub-item to a requirement
- `PUT /requirements/{id}/subitems/{subId}` - Update a sub-item
- `DELETE /requirements/{id}/subitems/{subId}` - Delete a sub-item

### Health Check
- `GET /health` - Service health check endpoint

## Data Models

### Requirement
```json
{
  "id": "req-1",
  "name": "Requirement Name",
  "description": "Requirement Description",
  "status": "pending",
  "technologies": ["Go", "JWT"],
  "subItems": [
    {
      "id": "sub-1",
      "name": "Sub-item Name",
      "status": "complete"
    }
  ],
  "createdAt": "",
  "updatedAt": ""
}
```

## Service Registration

The service automatically registers with the host-server at startup and performs periodic heartbeats every 30 seconds.

## Database Setup

The service uses MySQL as database with credentials matching scripts/start-mysql.sh:

**Default Configuration:**
- Host: localhost
- Port: 3306
- User: root
- Password: rootpass
- Database: projman_service

**Manual Setup:**
```sql
CREATE DATABASE projman_service CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

**Docker Setup:**
```bash
# Start MySQL with correct credentials
docker run --name my-mysql -e MYSQL_ROOT_PASSWORD=rootpass -p 3306:3306 -d mysql:8.0

# Or use Docker Compose
docker-compose up mysql -d
```

## Environment Variables

### Database Configuration
- `DB_HOST` - MySQL host (default: localhost)
- `DB_PORT` - MySQL port (default: 3306)
- `DB_USER` - MySQL username (default: root)
- `DB_PASSWORD` - MySQL password (default: "")
- `DB_NAME` - Database name (default: projman)

### Service Configuration
- `PORT` - Port to run the service on (default: 9094)
- `SERVICE_HOST` - Host for the service (default: localhost)
- `SERVICE_REGISTRY_URL` - URL for the host-server registry (default: http://localhost:8085/api/registry)

## Running the Service

```bash
# Build the service
go build -o projman .

# Run the service
./projman
```

Or run directly:
```bash
go run *.go
```

## Example Usage

### Create a Project
```bash
curl -X POST http://localhost:9094/projects \
  -H "Content-Type: application/json" \
  -d '{
    "name": "E-commerce Platform"
  }'
```

### Create a Subsystem
```bash
curl -X POST http://localhost:9094/subsystems \
  -H "Content-Type: application/json" \
  -d '{
    "name": "User Management",
    "projectId": 1
  }'
```

### Create a Feature
```bash
curl -X POST http://localhost:9094/features \
  -H "Content-Type: application/json" \
  -d '{
    "name": "User Registration",
    "subsystemId": 1
  }'
```

### Create a Requirement
```bash
curl -X POST http://localhost:9094/requirements \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Implement User Registration",
    "description": "Create user registration with email verification",
    "technologies": ["Go", "MySQL", "JWT"],
    "projectId": 1,
    "subsystemId": 1,
    "featureId": 1,
    "status": "pending"
  }'
```

### Add a Sub-item
```bash
curl -X POST http://localhost:9094/requirements/1/subitems \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Create registration endpoint",
    "status": "pending"
  }'
```

### Get Projects with Subsystems
```bash
curl http://localhost:9094/projects/1
```

### Get Subsystems by Project
```bash
curl http://localhost:9094/projects/1/subsystems
```

### Get Features by Subsystem
```bash
curl http://localhost:9094/subsystems/1/features
```

## Docker

The service can also be run with Docker:

```bash
# Build the image
docker build -t projman .

# Run the container
docker run -p 9094:9094 projman
```