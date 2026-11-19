# Projman Startup Scripts

This document explains how to use the startup scripts to run the Projman backend and UI without Docker.

## Prerequisites

Before running these scripts, ensure you have the following installed:

### Backend (Go service)
- Go 1.19 or higher
- MySQL server running and accessible

### UI (Angular application)
- Node.js 18+ (or Bun as an alternative)
- npm (if not using Bun)

## Scripts

### `start-backend.sh`
Starts the Go backend service that connects to MySQL and provides the API.

```bash
./scripts/start-backend.sh
```

### `start-ui.sh`
Starts the Angular UI development server that connects to the backend API.

```bash
./scripts/start-ui.sh
```

### `start-fullstack.sh`
Starts both the backend and UI services. The backend runs in the background while the UI runs in the foreground.

```bash
./scripts/start-fullstack.sh
```

## Configuration

### Backend Configuration
The backend service reads configuration from the `.env` file in the project root. Key variables include:

- `PORT`: Port for the service to run on (default: 8080)
- `SERVICE_HOST`: Host for the service (default: localhost)
- `SERVICE_REGISTRY_URL`: URL for the host-server registry (default: http://localhost:8085/api/registry)

Database configuration:
- `DB_HOST`: MySQL host (default: localhost)
- `DB_PORT`: MySQL port (default: 3306)
- `DB_USER`: MySQL username (default: root)
- `DB_PASSWORD`: MySQL password (default: rootpass)
- `DB_NAME`: Database name (default: projman_service)

### UI Configuration
The UI connects to the backend API at `http://localhost:8073` by default. This can be changed in `ui/src/services/requirements-api.service.ts`.

## Troubleshooting

### Backend Issues
- Ensure MySQL is running before starting the backend
- Check that the database credentials in `.env` match your MySQL setup
- Verify that the required port is not already in use

### UI Issues
- Make sure the backend service is running before starting the UI
- If using npm instead of Bun, ensure all dependencies are installed properly
- Check that the API URL in the service matches the running backend

## Development Notes

The UI includes comprehensive CRUD functionality for:
- Projects
- Subsystems
- Features
- Requirements
- Sub-items

All components have been implemented with proper API integration, forms, and data handling.