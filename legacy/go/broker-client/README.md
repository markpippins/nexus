# Atomic Broker Gateway Go SDK

Lightweight Go client for interacting with Atomic Broker Gateway services.

## Installation

```bash
go mod init your-service
go mod tidy
```

## Quick Start

```go
package main

import (
    "log"
    "github.com/atomic/broker-sdk"
)

func main() {
    // Create client
    client := broker.NewClient("http://localhost:8080", "http://localhost:8085")
    
    // Register a service
    service := broker.ServiceDetails{
        ServiceName: "go-microservice",
        Endpoint:    "http://localhost:3003",
        HealthCheck: "health",
        Framework:   "Gin",
    }
    
    success, err := client.RegisterService(service)
    if err != nil {
        log.Fatal(err)
    }
    
    if success {
        log.Println("Service registered successfully!")
    }
    
    // Invoke operation
    response, err := client.InvokeOperation(
        "getUserRegistrationForToken",
        map[string]interface{}{"token": "sample-token"},
        "",
    )
    
    if err != nil {
        log.Fatal(err)
    }
    
    if response.Success {
        log.Printf("Success: %+v", response.Data)
    } else {
        log.Printf("Error: %+v", response.Errors)
    }
}
```

## API Reference

### HTTPClient

Main HTTP client wrapper for broker gateway operations.

#### NewHTTPClient(baseURL, hostServerURL string) *HTTPClient

Creates a new HTTP client.

**Parameters:**
- `baseURL`: URL of the broker gateway
- `hostServerURL`: URL of the host server for service discovery

```go
client := broker.NewHTTPClient("http://localhost:8080", "http://localhost:8085")
```

### Methods

##### (c *HTTPClient) DiscoverService(operation string) (*ServiceDetails, error)

Find a service that can handle the specified operation.

**Parameters:**
- `operation`: Operation name (e.g., "getUserRegistrationForToken")

**Returns:** `*ServiceDetails` if found, `error` otherwise

```go
service, err := client.DiscoverService("getUserRegistrationForToken")
if err != nil {
    log.Fatal(err)
}
if service != nil {
    log.Printf("Found service: %s at %s", service.ServiceName, service.Endpoint)
}
```

##### (c *HTTPClient) GetServiceDetails(serviceName string) (*ServiceDetails, error)

Get detailed information about a specific service.

**Parameters:**
- `serviceName`: Name of the service

**Returns:** `*ServiceDetails` if found, `error` otherwise

```go
details, err := client.GetServiceDetails("loginService")
if err != nil {
    log.Fatal(err)
}
if details != nil {
    log.Printf("Service endpoint: %s", details.Endpoint)
}
```

##### (c *HTTPClient) InvokeOperation(operation string, params map[string]interface{}, serviceName string) (*BrokerResponse, error)

Invoke an operation on a service through the broker gateway.

**Parameters:**
- `operation`: Operation name to invoke
- `params`: Parameters for the operation
- `serviceName`: Optional service name (discovered if not provided)

**Returns:** `*BrokerResponse` with operation results

```go
response, err := client.InvokeOperation(
    "getUserRegistrationForToken",
    map[string]interface{}{"token": "sample-token"},
    "loginService",  // Optional - auto-discovered if not provided
)
if err != nil {
    log.Fatal(err)
}
if response.Success {
    log.Printf("Success: %+v", response.Data)
} else {
    log.Printf("Error: %+v", response.Errors)
}
```

##### (c *HTTPClient) HealthCheck(serviceName string) (bool, error)

Perform a health check on a specific service.

**Parameters:**
- `serviceName`: Name of the service to check

**Returns:** `bool` if healthy, `error` otherwise

```go
isHealthy, err := client.HealthCheck("loginService")
if err != nil {
    log.Fatal(err)
}
log.Printf("Login service healthy: %v", isHealthy)
```

##### (c *HTTPClient) RegisterService(serviceDetails ServiceDetails) (bool, error)

Register a new service with the broker gateway.

**Parameters:**
- `serviceDetails`: ServiceDetails struct containing service information

**Returns:** `bool` if registration successful, `error` otherwise

```go
service := broker.ServiceDetails{
    ServiceName: "my-service",
    Endpoint:    "http://localhost:3000",
    HealthCheck: "health",
    Framework:   "Gin",
}
success, err := client.RegisterService(service)
if err != nil {
    log.Fatal(err)
}
if success {
    log.Println("Service registered successfully!")
}
```

##### (c *HTTPClient) GetGatewayHealth() (*BrokerResponse, error)

Check the health of the broker gateway itself.

**Returns:** `*BrokerResponse` with gateway health status

```go
response, err := client.GetGatewayHealth()
if err != nil {
    log.Fatal(err)
}
if response.Success {
    log.Printf("Gateway healthy: %+v", response.Data)
} else {
    log.Printf("Gateway error: %+v", response.Errors)
}
```

## Data Models

### ServiceDetails

Contains service information from the broker gateway.

```go
type ServiceDetails struct {
    ServiceName   string `json:"serviceName"`
    Endpoint      string `json:"endpoint"`
    HealthCheck   string `json:"healthCheck,omitempty"`
    Framework     string `json:"framework,omitempty"`
    Status        string `json:"status,omitempty"`
    Operations    string `json:"operations,omitempty"`
}
```

### BrokerResponse

Response from broker gateway operations.

```go
type BrokerResponse struct {
    Success    bool                   `json:"success"`
    Data       interface{}            `json:"data,omitempty"`
    Errors     []BrokerError           `json:"errors,omitempty"`
    StatusCode int                   `json:"-"`
    RawResponse string             `json:"-"`
}
```

### BrokerError

Error information from broker gateway responses.

```go
type BrokerError struct {
    Code    string `json:"code"`
    Message string `json:"message"`
}
```

## Error Handling

All SDK operations return structured error information:

```go
if !response.Success {
    for _, err := range response.Errors {
        log.Printf("Error %s: %s", err.Code, err.Message)
    }
}
```

Common error codes:
- `SERVICE_NOT_FOUND`: No service found for operation
- `SERVICE_DETAILS_NOT_FOUND`: Could not get service details
- `OPERATION_FAILED`: HTTP operation failed
- `CLIENT_ERROR`: Client-side error (network, parsing, etc.)
- `GATEWAY_UNHEALTHY`: Gateway health check failed

## Integration Examples

### Gin Web Framework

```go
package main

import (
    "github.com/gin-gonic/gin"
    "github.com/atomic/broker-sdk"
    "log"
)

func main() {
    r := gin.Default()
    client := broker.NewClient("http://localhost:8080", "http://localhost:8085")
    
    r.POST("/users/token", func(c *gin.Context) {
        var request map[string]interface{}
        c.BindJSON(&request)
        
        // Delegate to login service through broker
        response, err := client.InvokeOperation(
            "getUserRegistrationForToken",
            request,
            "",
        )
        
        if err != nil {
            c.JSON(500, gin.H{"error": err.Error()})
            return
        }
        
        if response.Success {
            c.JSON(200, response.Data)
        } else {
            c.JSON(500, gin.H{"error": response.Errors})
        }
    })
    
    r.Run(":3000")
}
```

### Standard Library HTTP Server

```go
package main

import (
    "encoding/json"
    "log"
    "net/http"
    "github.com/atomic/broker-sdk"
)

func main() {
    client := broker.NewClient("http://localhost:8080", "http://localhost:8085")
    
    http.HandleFunc("/users/token", func(w http.ResponseWriter, r *http.Request) {
        var request map[string]interface{}
        json.NewDecoder(r.Body).Decode(&request)
        
        // Delegate to login service through broker
        response, err := client.InvokeOperation(
            "getUserRegistrationForToken",
            request,
            "",
        )
        
        if err != nil {
            log.Printf("Error: %v", err)
            http.Error(w, err.Error(), http.StatusInternalServerError)
            return
        }
        
        if response.Success {
            w.Header().Set("Content-Type", "application/json")
            json.NewEncoder(w).Encode(response.Data)
        } else {
            w.Header().Set("Content-Type", "application/json")
            w.WriteHeader(http.StatusInternalServerError)
            json.NewEncoder(w).Encode(response.Errors)
        }
    })
    
    log.Println("Server starting on :3000")
    log.Fatal(http.ListenAndServe(":3000", nil))
}
```

## Testing

The SDK can be easily tested with the existing Spring broker gateway:

```bash
# Start the broker gateway and host server
cd /path/to/spring
mvn spring-boot:run -pl broker-gateway &
mvn spring-boot:run -pl host-server &

# Run the Go SDK example
cd /path/to/go/service
go run main.go
```

This will register a Go service and test the complete request flow through the broker gateway.