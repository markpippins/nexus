package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

// ServiceRegistration represents the payload for registering with host-server
type ServiceRegistration struct {
	ServiceName string            `json:"serviceName"`
	Operations  []string          `json:"operations"`
	Endpoint    string            `json:"endpoint"`
	HealthCheck string            `json:"healthCheck"`
	Metadata    map[string]string `json:"metadata"`
	Framework   string            `json:"framework"`
	Version     string            `json:"version"`
	Port        int               `json:"port"`
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8073"
	}

	// Initialize database
	InitDatabase()

	// Seed sample data
	SeedData()

	// Set up routes using our custom router
	router := NewRouter()

	// Start the service
	servicePort := 9094 // Default port since 8080 is taken by broker-gateway
	if port != "" {
		fmt.Sscanf(port, "%d", &servicePort)
	}

	// Register with host-server
	serviceHost := os.Getenv("SERVICE_HOST")
	if serviceHost == "" {
		serviceHost = "localhost"
	}

	endpoint := fmt.Sprintf("http://%s:%d", serviceHost, servicePort)
	registryURL := os.Getenv("SERVICE_REGISTRY_URL")
	if registryURL == "" {
		registryURL = "http://localhost:8085/api/registry"
	}

	fmt.Printf("🚀 Starting projman service on port %s\n", port)
	fmt.Printf("🌐 Service will be available at: http://localhost:%s\n", port)
	fmt.Printf("💚 Health check: http://localhost:%s/health\n", port)

	// Register with host-server in a goroutine
	go func() {
		log.Printf("🚀 Starting registration service...")
		registerWithHostServer(registryURL, endpoint)

		// Set up periodic registration (like a heartbeat)
		ticker := time.NewTicker(30 * time.Second) // Re-register every 30 seconds
		defer ticker.Stop()

		log.Printf("💓 Starting registration heartbeat (every 30 seconds)")
		for range ticker.C {
			registerWithHostServer(registryURL, endpoint)
		}
	}()

	log.Printf("🌍 HTTP server starting on port %s", port)
	log.Fatal(http.ListenAndServe(":"+port, router))
}

func registerWithHostServer(registryURL, endpoint string) {
	log.Printf("🔄 Starting service registration...")
	log.Printf("📋 Registry URL: %s", registryURL)
	log.Printf("🌐 Service Endpoint: %s", endpoint)

	// Extract port from endpoint
	var port int
	// Parse servicePort from environment variable first
	servicePortStr := os.Getenv("PORT")
	if servicePortStr == "" {
		servicePortStr = "8073"
	}
	if p, err := strconv.Atoi(servicePortStr); err == nil {
		port = p
	} else {
		// Fallback to parsing from endpoint
		n, err := fmt.Sscanf(endpoint, "http://%*[^:]:%d", &port)
		if err != nil || n != 1 {
			log.Printf("❌ Error parsing endpoint %s for port: %v", endpoint, err)
			log.Printf("🔧 Using default port 9094")
			port = 9094
		} else {
			log.Printf("🔢 Extracted port: %d", port)
		}
	}
	log.Printf("🔢 Using port for registration: %d", port)

	registration := ServiceRegistration{
		ServiceName: "projman-service",
		Operations: []string{
			"getReqs", "createReq", "updateReq", "deleteReq", "addSubItem", "updateSubItem", "deleteSubItem",
			"getProjects", "createProject", "updateProject", "deleteProject",
			"getSubsystems", "createSubsystem", "updateSubsystem", "deleteSubsystem",
			"getFeatures", "createFeature", "updateFeature", "deleteFeature",
		},
		Endpoint:    endpoint,
		HealthCheck: endpoint + "/health",
		Metadata: map[string]string{
			"type":     "requirements-management-service",
			"language": "go",
			"database": "mysql",
		},
		Framework: "Go",
		Version:   "2.0.0",
		Port:      port,
	}

	log.Printf("📝 Preparing registration payload...")
	log.Printf("🏷️  Service Name: %s", registration.ServiceName)
	log.Printf("⚡ Operations count: %d", len(registration.Operations))
	log.Printf("🔗 Health Check: %s", registration.HealthCheck)
	log.Printf("📋 Operations: %v", registration.Operations)

	jsonData, err := json.Marshal(registration)
	if err != nil {
		log.Printf("❌ Error marshaling registration data: %v", err)
		return
	}
	log.Printf("📦 Registration payload size: %d bytes", len(jsonData))

	registerURL := registryURL + "/register"
	log.Printf("🚀 Sending registration request to: %s", registerURL)

	resp, err := http.Post(registerURL, "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		log.Printf("❌ Error registering with host-server: %v", err)
		log.Printf("🔍 Registry URL accessible? Check if host-server is running at %s", registryURL)
		return
	}
	defer resp.Body.Close()

	log.Printf("📬 Registration response status: %d", resp.StatusCode)

	body, _ := io.ReadAll(resp.Body)
	log.Printf("📋 Registration response body: %s", string(body))

	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		log.Printf("✅ Successfully registered with host-server")
		log.Printf("🎯 Service will be available at operations: %v", registration.Operations)
	} else {
		log.Printf("❌ Failed to register with host-server")
		log.Printf("📊 Status Code: %d", resp.StatusCode)
		log.Printf("📄 Response: %s", string(body))
		log.Printf("🔧 Possible causes:")
		log.Printf("   - host-server not running at %s", registryURL)
		log.Printf("   - network connectivity issues")
		log.Printf("   - invalid registration payload")
		log.Printf("   - host-server internal errors")
	}
}
