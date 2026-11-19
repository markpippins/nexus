package broker

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"time"
)

// ServiceDetails represents service details from broker gateway
type ServiceDetails struct {
	ServiceName   string `json:"serviceName"`
	Endpoint    string `json:"endpoint"`
	HealthCheck string `json:"healthCheck,omitempty"`
	Framework   string `json:"framework,omitempty"`
	Status      string `json:"status,omitempty"`
	Operations  string `json:"operations,omitempty"`
}

// BrokerRequest represents a request to broker gateway
type BrokerRequest struct {
	ServiceName string            `json:"serviceName"`
	Operation  string            `json:"operation"`
	Params     map[string]interface{} `json:"params"`
	RequestID  string             `json:"requestId,omitempty"`
}

// BrokerResponse represents a response from broker gateway
type BrokerResponse struct {
	Success    bool                   `json:"success"`
	Data       interface{}            `json:"data,omitempty"`
	Errors     []BrokerError           `json:"errors,omitempty"`
	StatusCode int                   `json:"-"`
	RawResponse string             `json:"-"`
}

type BrokerError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// HTTPClient wraps http.Client with common functionality
type HTTPClient struct {
	client      *http.Client
	BaseURL     string
	HostServerURL string
}

// NewHTTPClient creates a new HTTP client
func NewHTTPClient(baseURL, hostServerURL string) *HTTPClient {
	return &HTTPClient{
		client:  &http.Client{Timeout: 30 * time.Second},
		BaseURL: strings.TrimSuffix(baseURL, "/"),
		HostServerURL: strings.TrimSuffix(hostServerURL, "/"),
	}
}

// DiscoverService finds a service that can handle the specified operation
func (c *HTTPClient) DiscoverService(operation string) (*ServiceDetails, error) {
	log.Printf("Discovering service for operation: %s", operation)
	
	url := fmt.Sprintf("%s/api/registry/services/by-operation/%s", c.HostServerURL, operation)
	resp, err := c.client.Get(url)
	if err != nil {
		log.Printf("Service discovery failed: %v", err)
		return nil, err
	}
	defer resp.Body.Close()
	
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("service discovery failed with status %d", resp.StatusCode)
	}
	
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	
	var result struct {
		Data *ServiceDetails `json:"data"`
	}
	
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, err
	}
	
	if result.Data != nil {
		log.Printf("Found service %s for operation %s", result.Data.ServiceName, operation)
		return result.Data, nil
	}
	
	return nil, fmt.Errorf("service not found for operation: %s", operation)
}

// GetServiceDetails gets detailed information about a specific service
func (c *HTTPClient) GetServiceDetails(serviceName string) (*ServiceDetails, error) {
	log.Printf("Getting details for service: %s", serviceName)
	
	url := fmt.Sprintf("%s/api/registry/services/%s/details", c.HostServerURL, serviceName)
	resp, err := c.client.Get(url)
	if err != nil {
		log.Printf("Failed to get service details: %v", err)
		return nil, err
	}
	defer resp.Body.Close()
	
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("service details failed with status %d", resp.StatusCode)
	}
	
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	
	var serviceDetails ServiceDetails
	if err := json.Unmarshal(body, &serviceDetails); err != nil {
		return nil, err
	}
	
	log.Printf("Retrieved details for service %s with endpoint %s", serviceName, serviceDetails.Endpoint)
	return &serviceDetails, nil
}

// InvokeOperation invokes an operation on a service through broker gateway
func (c *HTTPClient) InvokeOperation(operation string, params map[string]interface{}, serviceName string) (*BrokerResponse, error) {
	var service *ServiceDetails
	var err error
	
	if serviceName == "" {
		service, err = c.DiscoverService(operation)
		if err != nil || service == nil {
			return &BrokerResponse{
				Success: false,
				Errors: []BrokerError{{Code: "SERVICE_NOT_FOUND", Message: fmt.Sprintf("No service found for operation: %s", operation)}},
				StatusCode: 404,
			}, nil
		}
		serviceName = service.ServiceName
	} else {
		service, err = c.GetServiceDetails(serviceName)
		if err != nil || service == nil {
			return &BrokerResponse{
				Success: false,
				Errors: []BrokerError{{Code: "SERVICE_DETAILS_NOT_FOUND", Message: fmt.Sprintf("Could not get details for service: %s", serviceName)}},
				StatusCode: 500,
			}, nil
		}
	}
	
	// Build operation URL
	endpoint := service.Endpoint
	var operationURL string
	if endpoint != "" {
		operationURL = fmt.Sprintf("%s/%s", endpoint, operation)
	} else {
		operationURL = "/" + operation
	}
	
	log.Printf("Invoking operation %s on service %s", operation, serviceName)
	
	// Prepare request body
	request := BrokerRequest{
		ServiceName: serviceName,
		Operation:  operation,
		Params:     params,
	}
	
	requestBody, err := json.Marshal(request)
	if err != nil {
		return &BrokerResponse{
			Success: false,
			Errors: []BrokerError{{Code: "REQUEST_ENCODING_ERROR", Message: err.Error()}},
			StatusCode: 500,
		}, nil
	}
	
	// Create HTTP request
	fullURL := c.BaseURL + operationURL
	resp, err := c.client.Post(fullURL, "application/json", bytes.NewBuffer(requestBody))
	if err != nil {
		return &BrokerResponse{
			Success: false,
			Errors: []BrokerError{{Code: "CLIENT_ERROR", Message: err.Error()}},
			StatusCode: 500,
		}, nil
	}
	defer resp.Body.Close()
	
	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return &BrokerResponse{
			Success: false,
			Errors: []BrokerError{{Code: "RESPONSE_READ_ERROR", Message: err.Error()}},
			StatusCode: 500,
		}, nil
	}
	
	brokerResponse := &BrokerResponse{
		StatusCode: resp.StatusCode,
		RawResponse: string(responseBody),
	}
	
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		if err := json.Unmarshal(responseBody, &brokerResponse); err == nil {
			log.Printf("Successfully invoked %s on %s", operation, serviceName)
			brokerResponse.Success = true
		}
	} else {
		log.Printf("Operation failed with status %d", resp.StatusCode)
		brokerResponse.Success = false
		brokerResponse.Errors = []BrokerError{{
			{Code: "OPERATION_FAILED", Message: fmt.Sprintf("HTTP %d: %s", resp.StatusCode, string(responseBody))},
		}
	}
	
	return brokerResponse, nil
}

// HealthCheck performs health check on a specific service
func (c *HTTPClient) HealthCheck(serviceName string) (bool, error) {
	serviceDetails, err := c.GetServiceDetails(serviceName)
	if err != nil || serviceDetails == nil {
		return false, fmt.Errorf("service not found: %s", serviceName)
	}
	
	healthURL := serviceDetails.HealthCheck
	if healthURL == "" {
		// Default to /health endpoint
		healthURL = fmt.Sprintf("%s/health", serviceDetails.Endpoint)
	} else if !strings.HasPrefix(healthURL, "/") {
		if strings.HasSuffix(serviceDetails.Endpoint, "/") {
			healthURL = fmt.Sprintf("%s%s", serviceDetails.Endpoint, healthURL)
		} else {
			healthURL = fmt.Sprintf("%s/%s", serviceDetails.Endpoint, healthURL)
		}
	}
	
	log.Printf("Health checking %s at %s", serviceName, healthURL)
	
	resp, err := c.client.Get(healthURL)
	if err != nil {
		log.Printf("Health check failed for %s: %v", serviceName, err)
		return false, err
	}
	defer resp.Body.Close()
	
	isHealthy := resp.StatusCode == 200
	log.Printf("Health check for %s: %s", serviceName, map[bool]string{true: "healthy", false: "unhealthy"}[isHealthy])
	
	return isHealthy, nil
}

// RegisterService registers a new service with the broker gateway
func (c *HTTPClient) RegisterService(serviceDetails ServiceDetails) (bool, error) {
	log.Printf("Registering service: %s", serviceDetails.ServiceName)
	
	url := fmt.Sprintf("%s/api/registry/services", c.HostServerURL)
	
	requestData, err := json.Marshal(struct {
		Name        string `json:"name"`
		Endpoint    string `json:"endpoint"`
		HealthCheck string `json:"healthCheck,omitempty"`
		Framework   string `json:"framework,omitempty"`
		Operations  string `json:"operations,omitempty"`
	}{
		Name:        serviceDetails.ServiceName,
		Endpoint:    serviceDetails.Endpoint,
		HealthCheck: serviceDetails.HealthCheck,
		Framework:   serviceDetails.Framework,
		Operations:  serviceDetails.Operations,
	})
	if err != nil {
		log.Printf("Service registration failed: %v", err)
		return false, err
	}
	
	resp, err := c.client.Post(url, "application/json", bytes.NewBuffer(requestData))
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	
	success := resp.StatusCode == 200 || resp.StatusCode == 201
	if success {
		log.Printf("Successfully registered service: %s", serviceDetails.ServiceName)
	} else {
		log.Printf("Failed to register service: %d %s", resp.StatusCode, resp.Status)
	}
	
	return success, nil
}

// GetGatewayHealth checks the health of the broker gateway itself
func (c *HTTPClient) GetGatewayHealth() (*BrokerResponse, error) {
	url := fmt.Sprintf("%s/health", c.BaseURL)
	resp, err := c.client.Get(url)
	if err != nil {
		return &BrokerResponse{
			Success: false,
			Errors: []BrokerError{{Code: "HEALTH_CHECK_ERROR", Message: err.Error()}},
			StatusCode: 500,
		}, nil
	}
	defer resp.Body.Close()
	
	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return &BrokerResponse{
			Success: false,
			Errors: []BrokerError{{Code: "RESPONSE_READ_ERROR", Message: err.Error()}},
			StatusCode: 500,
		}, nil
	}
	
	brokerResponse := &BrokerResponse{
		StatusCode: resp.StatusCode,
		RawResponse: string(responseBody),
	}
	
	if resp.StatusCode == 200 {
		if err := json.Unmarshal(responseBody, &brokerResponse); err == nil {
			brokerResponse.Success = true
		}
	} else {
		brokerResponse.Success = false
		brokerResponse.Errors = []BrokerError{{
			{Code: "GATEWAY_UNHEALTHY", Message: fmt.Sprintf("Gateway health check failed: %d", resp.StatusCode)},
		}
	}
	
	return brokerResponse, nil
}

// NewClient creates a pre-configured broker gateway client
func NewClient(baseURL, hostServerURL string) *HTTPClient {
	return NewHTTPClient(baseURL, hostServerURL)
}