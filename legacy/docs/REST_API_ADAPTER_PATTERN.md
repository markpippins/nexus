# REST API Adapter Pattern

## Overview

The **REST API Adapter Pattern** allows you to integrate **any** REST API (Python, Node.js, Go, Ruby, .NET, etc.) into the Atomic Platform's broker ecosystem without modifying the original service.

## The Problem

You have existing REST APIs that:
- Use different frameworks (FastAPI, Express, Flask, etc.)
- Have their own endpoint structures
- Aren't compatible with the broker protocol
- Can't be modified (legacy, third-party, etc.)

## The Solution

Create a thin **adapter** that:
1. Wraps the REST API
2. Registers with host-server
3. Maps broker operations to REST endpoints
4. Forwards requests and returns responses

## Architecture

```
┌─────────────────┐
│ Angular Client  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Broker Gateway  │ (Port 8080)
└────────┬────────┘
         │ Query: "Which service handles 'startScan'?"
         ▼
┌─────────────────┐
│  Host Server    │ (Port 8085)
└────────┬────────┘
         │ Response: "fsCrawlerService at localhost:8001"
         ▼
┌─────────────────┐
│  REST Adapter   │ (Port 8001) ← Thin wrapper
└────────┬────────┘
         │ Maps: startScan → POST /scan/start
         ▼
┌─────────────────┐
│  Original API   │ (Port 8000) ← Unchanged
│  (fs-crawler)   │
└─────────────────┘
```

## Example: FS Crawler Adapter

### Original Service (fs-crawler)

FastAPI service with REST endpoints:
```python
@router.post("/scan/start")
async def start_scan(path: Optional[str] = None):
    # Scan files
    
@router.get("/search")
async def search_files(q: str, file_type: str):
    # Search files
    
@router.get("/duplicates/groups")
async def get_duplicate_groups(method: str):
    # Find duplicates
```

### Adapter Implementation

```python
# python/fs-crawler-adapter/adapter.py
from fastapi import FastAPI
import httpx

app = FastAPI()

# Register with host-server on startup
@app.on_event("startup")
async def register_service():
    registration = {
        "serviceName": "fsCrawlerService",
        "operations": ["startScan", "searchFiles", "getDuplicates"],
        "endpoint": "http://localhost:8001",
        "healthCheck": "http://localhost:8001/health",
        "framework": "FastAPI-Adapter",
        "port": 8001
    }
    
    async with httpx.AsyncClient() as client:
        await client.post(
            "http://localhost:8085/api/registry/register",
            json=registration
        )

# Map broker operations to REST endpoints
@app.post("/api/broker/execute")
async def execute_operation(request: dict):
    operation = request["operation"]
    params = request["params"]
    
    async with httpx.AsyncClient() as client:
        if operation == "startScan":
            # Map to POST /scan/start
            response = await client.post(
                "http://localhost:8000/scan/start",
                params={"path": params.get("path")}
            )
            
        elif operation == "searchFiles":
            # Map to GET /search
            response = await client.get(
                "http://localhost:8000/search",
                params={
                    "q": params.get("query"),
                    "file_type": params.get("fileType")
                }
            )
            
        elif operation == "getDuplicates":
            # Map to GET /duplicates/groups
            response = await client.get(
                "http://localhost:8000/duplicates/groups",
                params={"method": params.get("method", "fingerprint")}
            )
        
        return {"success": True, "data": response.json()}
```

## Usage

### 1. Start the Original Service
```bash
cd python/fs-crawler
python -m uvicorn app.main:app --port 8000
```

### 2. Start the Adapter
```bash
cd python/fs-crawler-adapter
python adapter.py
```

The adapter automatically registers with host-server.

### 3. Use Through Broker

```bash
curl -X POST http://localhost:8080/api/broker/submitRequest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "fsCrawlerService",
    "operation": "startScan",
    "params": {"path": "/media/music"}
  }'
```

## Creating Your Own Adapter

### Step 1: Copy the Template

```bash
cp -r python/fs-crawler-adapter python/my-service-adapter
cd python/my-service-adapter
```

### Step 2: Update Configuration

```python
# adapter.py
MY_SERVICE_URL = os.getenv("MY_SERVICE_URL", "http://localhost:9000")

@app.on_event("startup")
async def register_service():
    registration = {
        "serviceName": "myService",
        "operations": ["operation1", "operation2"],
        "endpoint": "http://localhost:8001",
        # ... rest of config
    }
```

### Step 3: Map Operations

```python
@app.post("/api/broker/execute")
async def execute_operation(request: dict):
    operation = request["operation"]
    params = request["params"]
    
    async with httpx.AsyncClient() as client:
        if operation == "operation1":
            response = await client.get(
                f"{MY_SERVICE_URL}/api/endpoint1",
                params=params
            )
            
        elif operation == "operation2":
            response = await client.post(
                f"{MY_SERVICE_URL}/api/endpoint2",
                json=params
            )
        
        return {"success": True, "data": response.json()}
```

## Real-World Examples

### Example 1: Weather API

```python
# Wrap OpenWeatherMap API
@app.post("/api/broker/execute")
async def execute_operation(request: dict):
    operation = request["operation"]
    params = request["params"]
    
    if operation == "getCurrentWeather":
        response = await client.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "q": params["city"],
                "appid": API_KEY
            }
        )
        
    elif operation == "getForecast":
        response = await client.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={
                "q": params["city"],
                "cnt": params.get("days", 5),
                "appid": API_KEY
            }
        )
```

### Example 2: Database API

```python
# Wrap a database REST API
@app.post("/api/broker/execute")
async def execute_operation(request: dict):
    operation = request["operation"]
    params = request["params"]
    
    if operation == "queryRecords":
        response = await client.post(
            f"{DB_API_URL}/query",
            json={
                "table": params["table"],
                "where": params.get("filter", {}),
                "limit": params.get("limit", 100)
            }
        )
        
    elif operation == "insertRecord":
        response = await client.post(
            f"{DB_API_URL}/insert",
            json={
                "table": params["table"],
                "data": params["record"]
            }
        )
```

### Example 3: Legacy SOAP Service

```python
# Wrap a SOAP service with REST adapter
import zeep

@app.post("/api/broker/execute")
async def execute_operation(request: dict):
    operation = request["operation"]
    params = request["params"]
    
    client = zeep.Client(SOAP_WSDL_URL)
    
    if operation == "getCustomer":
        result = client.service.GetCustomer(
            customerId=params["customerId"]
        )
        return {"success": True, "data": result}
```

## Benefits

### ✅ No Service Modification
- Original service stays unchanged
- No code changes required
- Works with third-party APIs

### ✅ Language Agnostic
- Wrap Python, Node.js, Go, Ruby, .NET
- Mix technologies freely
- Use best tool for each job

### ✅ Unified Discovery
- All services in host-server registry
- Single source of truth
- Centralized management

### ✅ Centralized Routing
- All traffic through broker-gateway
- Consistent security
- Unified monitoring

### ✅ Flexible Integration
- Internal services
- External APIs
- Legacy systems
- Third-party services

## Adapter Patterns

### Pattern 1: Simple Proxy
Direct 1:1 mapping of operations to endpoints.

### Pattern 2: Parameter Transformation
Transform broker parameters to match REST API format.

```python
if operation == "searchFiles":
    # Transform broker params to REST params
    rest_params = {
        "q": params.get("query"),           # query → q
        "file_type": params.get("fileType"), # fileType → file_type
        "limit": params.get("limit", 50)
    }
    response = await client.get(f"{API_URL}/search", params=rest_params)
```

### Pattern 3: Response Transformation
Transform REST response to broker format.

```python
response = await client.get(f"{API_URL}/data")
data = response.json()

# Transform response
return {
    "success": True,
    "data": {
        "items": data["results"],
        "total": data["count"],
        "page": data["page_number"]
    }
}
```

### Pattern 4: Aggregation
Combine multiple REST calls into one broker operation.

```python
if operation == "getUserProfile":
    # Call multiple endpoints
    user_response = await client.get(f"{API_URL}/users/{user_id}")
    posts_response = await client.get(f"{API_URL}/users/{user_id}/posts")
    
    # Combine results
    return {
        "success": True,
        "data": {
            "user": user_response.json(),
            "posts": posts_response.json()
        }
    }
```

## Testing

### Test Adapter Directly
```bash
curl -X POST http://localhost:8001/api/broker/execute \
  -H "Content-Type: application/json" \
  -d '{
    "operation": "startScan",
    "params": {"path": "/media"}
  }'
```

### Test Through Broker
```bash
curl -X POST http://localhost:8080/api/broker/submitRequest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "fsCrawlerService",
    "operation": "startScan",
    "params": {"path": "/media"}
  }'
```

### Health Check
```bash
curl http://localhost:8001/health
```

## Deployment

### Docker Compose
```yaml
services:
  fs-crawler:
    image: fs-crawler:latest
    ports:
      - "8000:8000"
  
  fs-crawler-adapter:
    image: fs-crawler-adapter:latest
    ports:
      - "8001:8001"
    environment:
      - FS_CRAWLER_URL=http://fs-crawler:8000
      - HOST_SERVER_URL=http://host-server:8085
    depends_on:
      - fs-crawler
      - host-server
```

## See Also

- [FS Crawler Adapter Implementation](../python/fs-crawler-adapter/README.md)
- [Host Server Documentation](../spring/host-server/README.md)
- [Service Registry Architecture](SERVICE_REGISTRY_ARCHITECTURE.md)
- [Moleculer Search Service](../node/moleculer-search/README.md) - Another integration example