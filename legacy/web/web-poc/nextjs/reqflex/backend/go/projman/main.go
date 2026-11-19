package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
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
		port = "8080"
	}

	// Set up routes using our custom router
	router := NewRouter()

	// Start the service
	servicePort := 9094  // Default port since 8080 is taken by broker-gateway
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

	fmt.Printf("Starting projman service on port %s\n", port)

	// Register with host-server in a goroutine
	go func() {
		registerWithHostServer(registryURL, endpoint)
		// Set up periodic registration (like a heartbeat)
		ticker := time.NewTicker(30 * time.Second) // Re-register every 30 seconds
		defer ticker.Stop()

		for range ticker.C {
			registerWithHostServer(registryURL, endpoint)
		}
	}()

	log.Fatal(http.ListenAndServe(":"+port, router))
}


func registerWithHostServer(registryURL, endpoint string) {
	// Extract port from endpoint
	var port int
	fmt.Sscanf(endpoint, "http://%*[^:]:%d", &port)

	registration := ServiceRegistration{
		ServiceName: "projman-service",
		Operations:  []string{"getRequirements", "createRequirement", "updateRequirement", "deleteRequirement", "addSubItem", "updateSubItem", "deleteSubItem"},
		Endpoint:    endpoint,
		HealthCheck: endpoint + "/health",
		Metadata: map[string]string{
			"type":     "requirements-status-service",
			"language": "go",
		},
		Framework: "Go",
		Version:   "1.0.0",
		Port:      port,
	}

	jsonData, err := json.Marshal(registration)
	if err != nil {
		log.Printf("Error marshaling registration data: %v", err)
		return
	}

	resp, err := http.Post(registryURL+"/register", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		log.Printf("Error registering with host-server: %v", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		body, _ := io.ReadAll(resp.Body)
		log.Printf("Successfully registered with host-server: %s", string(body))
	} else {
		body, _ := io.ReadAll(resp.Body)
		log.Printf("Failed to register with host-server. Status: %d, Response: %s", resp.StatusCode, string(body))
	}
}