# Environment Variable Management for Atomic Project

This document explains how to manage environment variables across all projects in the Atomic workspace.

## Overview

The Atomic project consists of multiple services (Node.js, Spring Boot, Next.js, etc.) that use various environment variables for configuration. This directory contains scripts to help manage and load these variables consistently.

## Environment Variables by Service

### Node.js Services
- **broker-service-proxy**: Uses `BROKER_PROXY_PORT`, `HOST`, `BROKER_GATEWAY_URL`
- **image-server**: Uses `IMAGE_SERVER_PORT`, `IMAGE_ROOT_DIR`
- **google/gapi-search-serv**: Uses `SEARCH_SERVER_PORT`, `GOOGLE_API_KEY`, `SEARCH_ENGINE_ID`
- **file-system-server**: Uses `FS_SERVER_PORT`, `FS_ROOT_DIR`
- **unsplash/image-search**: Uses `UNSPLASH_SERVER_PORT`, `UNSPLASH_ACCESS_KEY`

### Next.js Web Applications
- Uses `NEXT_PUBLIC_*` prefixed variables for client-side access
- Common variables: `NEXT_PUBLIC_BROKER_GATEWAY_URL`, `NEXT_PUBLIC_DEBUG_MODE`, `NEXT_PUBLIC_API_URL`

### Spring Boot Services (Docker)
- Database configs: `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`, `SPRING_DATASOURCE_PASSWORD`
- MongoDB: `SPRING_DATA_MONGODB_URI`

## Environment Management Scripts

### setup-env.sh
This script loads environment variables from .env files and sets defaults for all services:

```bash
cd /mnt/c/dev/WORK/atomic
source ./setup-env.sh
```

The script will:
1. Load variables from `.env` if it exists in the root
2. Load service-specific .env files (like `./node/broker-service-proxy/.env.local`)
3. Set default values for any missing variables
4. Display current settings

### check-env.sh
This script checks which environment variables are set and which are missing:

```bash
cd /mnt/c/dev/WORK/atomic
./check-env.sh
```

### .env.example
This file contains all possible environment variables with documentation. Copy it to create your own `.env` file:

```bash
cp .env.example .env
# Edit .env with your specific values
```

## Customizing Environment Variables

1. Copy the example file: `cp .env.example .env`
2. Edit the `.env` file with your specific values
3. Run the setup script: `source ./setup-env.sh`
4. Verify with: `./check-env.sh`

## Important Notes

- Sensitive variables like API keys are marked as "sensitive" and their values are hidden during checks
- The setup script will respect existing environment variables and only set defaults for missing ones
- Variables in service-specific .env files (like `node/broker-service-proxy/.env.local`) take precedence over defaults
- For Docker Compose deployments, these environment variables will also be available in the container environment