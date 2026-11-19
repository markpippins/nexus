# Services Console Backend

Node.js REST API backend for the Services Console application, built with Express, Prisma, and MySQL.

## 🚀 Features

- **RESTful API**: Full CRUD operations for all entities
- **Prisma ORM**: Type-safe database access with auto-generated client
- **MySQL Database**: Production-ready relational database
- **Related Data**: Includes relationships in API responses
- **Soft Deletes**: Data preservation with `active_flag` field
- **CORS Support**: Cross-origin requests enabled
- **Health Check**: Endpoint for monitoring service status

## 📋 API Endpoints

### Base URL: `http://localhost:3001/api`

### Core Entities
- `GET/POST /languages` - Languages catalog
- `GET/POST /categories` - Application categories  
- `GET/POST /vendors` - Software vendors
- `GET/POST /frameworks` - Development frameworks
- `GET/POST /operatingSystems` - Operating systems
- `GET/POST /environmentTypes` - Environment types (Dev/Staging/Prod)
- `GET/POST /serverTypes` - Server type classifications
- `GET/POST /serviceTypes` - Service type classifications
- `GET/POST /configTypes` - Configuration type classifications

### Infrastructure Entities
- `GET/POST /servers` - Physical and virtual servers
- `GET/POST /services` - Application services
- `GET/POST /serviceConfigs` - Service configuration parameters
- `GET/POST /serviceDependencies` - Inter-service dependencies
- `GET/POST /deployments` - Service deployment records

### Operations
- `GET /{entity}` - Get all active records
- `GET /{entity}/:id` - Get specific record by ID
- `POST /{entity}` - Create new record
- `PUT /{entity}/:id` - Update existing record
- `DELETE /{entity}/:id` - Soft delete record (sets active_flag=false)

### Health Check
- `GET /health` - Service health status

## 🛠️ Setup Instructions

### Prerequisites
- Node.js 18+ 
- MySQL 8.0+
- npm or yarn

### 1. Install Dependencies
```bash
cd node/services-console-backend
npm install
```

### 2. Configure Database
```bash
# Create MySQL database (using credentials from project .env.example)
mysql -u root -prootpass
CREATE DATABASE services_console;

# Copy environment file
cp .env.example .env
# Database is pre-configured with: mysql://root:rootpass@localhost:3306/services_console
```

### 3. Initialize Database
```bash
# Generate Prisma client
npm run build

# Create database schema
npm run db:push

# Seed database with sample data
npm run db:seed
```

### 4. Start Development Server
```bash
npm run dev
```

Server will start at `http://localhost:3001`

## 📊 Database Schema

The application manages a comprehensive services catalog with the following entities:

**Catalog Data:**
- `languages` - Programming languages
- `frameworks` - Development frameworks with vendor/category/language relationships
- `operatingSystems` - OS information with vendor relationships
- `vendors` - Software vendors and companies
- `categories` - Framework categories (Frontend/Backend/Infrastructure)

**Lookup Data:**
- `environmentTypes` - Dev/Staging/Production environments
- `serverTypes` - VM/Physical/Container classifications
- `serviceTypes` - Microservice/Monolith/Batch classifications
- `configTypes` - Database/API Key/Feature Flag types

**Infrastructure:**
- `servers` - Physical/virtual servers with OS, type, and environment relationships
- `services` - Application services with framework and type relationships
- `serviceConfigs` - Configuration parameters per service/environment
- `serviceDependencies` - Inter-service dependency mapping
- `deployments` - Service deployment tracking with version history

## 🔧 Development

### Generate Prisma Client
```bash
npm run build
```

### View Database
```bash
npm run db:studio
```

### Reset Database
```bash
npm run db:push --force-reset
npm run db:seed
```

## 📝 Example API Calls

### Get all servers with related data
```bash
curl http://localhost:3001/api/servers
```

### Create new service
```bash
curl -X POST http://localhost:3001/api/services \
  -H "Content-Type: application/json" \
  -d '{
    "name": "User Service",
    "description": "Manages user profiles and authentication",
    "framework_id": "f_ang",
    "service_type_id": "svt_micro",
    "default_port": 3001,
    "api_base_path": "/api/v1/users",
    "version": "1.0.0",
    "status": "Active"
  }'
```

### Update server
```bash
curl -X PUT http://localhost:3001/api/servers/srv_01 \
  -H "Content-Type: application/json" \
  -d '{
    "status": "Maintenance",
    "cpu_cores": 16
  }'
```

## 🔍 Testing

All endpoints return JSON responses with proper HTTP status codes:

- `200` - Success (GET/PUT)
- `201` - Created (POST)
- `404` - Not found
- `500` - Server error

## 📈 Production Considerations

1. **Authentication**: Add JWT/API key middleware
2. **Rate Limiting**: Implement request throttling
3. **Logging**: Add structured logging with Winston
4. **Validation**: Add request validation with Joi/Zod
5. **Caching**: Implement Redis for frequently accessed data
6. **Environment**: Use environment-specific configurations
7. **Security**: Add helmet, CORS policies, input sanitization

## 🤝 Integration with Angular Frontend

The API is designed to work seamlessly with the Angular Services Console:

1. Set `NODE_ENV=production` and `PORT=3001`
2. Update Angular `DbService` to call REST endpoints instead of using mock data
3. Configure CORS to allow Angular app origin
4. Toggle between debug mode (mock) and production mode (MySQL) in the Angular UI

## 📚 Technologies Used

- **Node.js** - Runtime environment
- **Express.js** - Web framework
- **Prisma** - Database ORM
- **MySQL** - Relational database
- **CORS** - Cross-origin resource sharing
- **dotenv** - Environment variable management