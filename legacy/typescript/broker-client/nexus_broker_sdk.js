/**
 * Nexus Broker Gateway Node.js SDK
 * Lightweight client library for Nexus Broker Gateway services
 */

const axios = require('axios');
const { URL } = require('url');

class ServiceDetails {
    constructor(data) {
        this.serviceName = data.serviceName || '';
        this.endpoint = data.endpoint || '';
        this.healthCheck = data.healthCheck || null;
        this.framework = data.framework || null;
        this.status = data.status || null;
        this.operations = data.operations || null;
    }
}

class BrokerResponse {
    constructor(options = {}) {
        this.success = options.success || false;
        this.data = options.data || null;
        this.errors = options.errors || null;
        this.statusCode = options.statusCode || 200;
        this.rawResponse = options.rawResponse || null;
    }
}

class BrokerGatewayClient {
    /**
     * Initialize broker gateway client
     * @param {string} options.gatewayUrl - URL of broker gateway
     * @param {string} options.hostServerUrl - URL of host server for service discovery
     */
    constructor(options = {}) {
        this.gatewayUrl = options.gatewayUrl?.replace(/\/$/, '') || 'http://localhost:8081';
        this.hostServerUrl = options.hostServerUrl?.replace(/\/$/, '') || 'http://localhost:8085';
        
        this.client = axios.create({
            baseURL: this.gatewayUrl,
            headers: {
                'Content-Type': 'application/json'
            },
            timeout: 30000
        });
    }
    
    /**
     * Discover service that can handle the specified operation
     * @param {string} operation - Operation name (e.g., "getUserRegistrationForToken")
     * @returns {Promise<ServiceDetails|null>}
     */
    async discoverService(operation) {
        try {
            const url = `${this.hostServerUrl}/api/v1/registry/services/by-operation/${operation}`;
            console.log(`Discovering service for operation: ${operation}`);
            
            const response = await this.client.get(url);
            if (response.status === 200 && response.data) {
                const serviceInfo = response.data.data || response.data;
                return new ServiceDetails(serviceInfo);
            }
        } catch (error) {
            console.error(`Service discovery failed: ${error.message}`);
        }
        
        return null;
    }
    
    /**
     * Get detailed information about a specific service
     * @param {string} serviceName - Name of the service
     * @returns {Promise<ServiceDetails|null>}
     */
    async getServiceDetails(serviceName) {
        try {
            const url = `${this.hostServerUrl}/api/v1/registry/services/${serviceName}/details`;
            console.log(`Getting details for service: ${serviceName}`);
            
            const response = await this.client.get(url);
            if (response.status === 200 && response.data) {
                const serviceData = response.data.data || response.data;
                return new ServiceDetails(serviceData);
            }
        } catch (error) {
            console.error(`Failed to get service details: ${error.message}`);
        }
        
        return null;
    }
    
    /**
     * Invoke an operation on a service through the broker gateway
     * @param {string} operation - Operation name to invoke
     * @param {Object} params - Parameters for the operation
     * @param {string} serviceName - Optional service name (discovered if not provided)
     * @returns {Promise<BrokerResponse>}
     */
    async invokeOperation(operation, params, serviceName = null) {
        try {
            // Discover service if not provided
            if (!serviceName) {
                const service = await this.discoverService(operation);
                if (!service) {
                    return new BrokerResponse({
                        errors: [{
                            code: 'SERVICE_NOT_FOUND',
                            message: `No service found for operation: ${operation}`
                        }],
                        statusCode: 404
                    });
                }
                serviceName = service.serviceName;
            }
            
            // Get service details
            const serviceDetails = await this.getServiceDetails(serviceName);
            if (!serviceDetails) {
                return new BrokerResponse({
                    errors: [{
                        code: 'SERVICE_DETAILS_NOT_FOUND',
                        message: `Could not get details for service: ${serviceName}`
                    }],
                    statusCode: 500
                });
            }
            
            // Build operation URL
            const endpoint = serviceDetails.endpoint;
            const operationUrl = endpoint.endsWith('/') ? `${endpoint}${operation}` : `${endpoint}/${operation}`;
            
            console.log(`Invoking operation ${operation} on service ${serviceName}`);
            
            // Invoke operation
            const response = await this.client.post(operationUrl, params);
            
            if (response.status >= 200 && response.status < 300) {
                console.log(`Successfully invoked ${operation} on ${serviceName}`);
                return new BrokerResponse({
                    success: true,
                    data: response.data,
                    statusCode: response.status,
                    rawResponse: JSON.stringify(response.data)
                });
            } else {
                console.warn(`Operation failed with status ${response.status}`);
                return new BrokerResponse({
                    errors: [{
                        code: 'OPERATION_FAILED',
                        message: `HTTP ${response.status}: ${JSON.stringify(response.data)}`
                    }],
                    statusCode: response.status,
                    rawResponse: JSON.stringify(response.data)
                });
            }
        } catch (error) {
            console.error(`Failed to invoke operation ${operation}: ${error.message}`);
            return new BrokerResponse({
                errors: [{
                    code: 'CLIENT_ERROR',
                    message: error.message
                }],
                statusCode: 500
            });
        }
    }
    
    /**
     * Perform health check on a specific service
     * @param {string} serviceName - Name of the service to check
     * @returns {Promise<boolean>}
     */
    async healthCheck(serviceName) {
        try {
            const serviceDetails = await this.getServiceDetails(serviceName);
            if (!serviceDetails) {
                return false;
            }
            
            let healthUrl = serviceDetails.healthCheck;
            if (!healthUrl) {
                // Default to /health endpoint
                healthUrl = serviceDetails.endpoint.endsWith('/') 
                    ? `${serviceDetails.endpoint}health` 
                    : `${serviceDetails.endpoint}/health`;
            } else if (!healthUrl.startsWith('/')) {
                healthUrl = serviceDetails.endpoint.endsWith('/')
                    ? `${serviceDetails.endpoint}${healthUrl}`
                    : `${serviceDetails.endpoint}/${healthUrl}`;
            }
            
            console.debug(`Health checking ${serviceName} at ${healthUrl}`);
            
            const response = await axios.get(healthUrl, { timeout: 10000 });
            const isHealthy = response.status === 200;
            console.debug(`Health check for ${serviceName}: ${isHealthy ? 'healthy' : 'unhealthy'}`);
            
            return isHealthy;
        } catch (error) {
            console.warn(`Health check failed for ${serviceName}: ${error.message}`);
            return false;
        }
    }
    
    /**
     * Register a new service with the broker gateway
     * @param {ServiceDetails} serviceDetails - Details of the service to register
     * @returns {Promise<boolean>}
     */
    async registerService(serviceDetails) {
        try {
            const url = `${this.hostServerUrl}/api/v1/registry/register`;
            console.log(`Registering service: ${serviceDetails.serviceName}`);
            
            const serviceData = {
                serviceName: serviceDetails.serviceName,
                endpoint: serviceDetails.endpoint,
                healthCheck: serviceDetails.healthCheck,
                framework: serviceDetails.framework,
                operations: serviceDetails.operations
            };
            
            const response = await this.client.post(url, serviceData);
            const success = response.status === 200 || response.status === 201;
            
            if (success) {
                console.log(`Successfully registered service: ${serviceDetails.serviceName}`);
            } else {
                console.error(`Failed to register service: ${response.status} ${response.statusText}`);
            }
            
            return success;
        } catch (error) {
            console.error(`Service registration failed: ${error.message}`);
            return false;
        }
    }
    
    /**
     * Check health of the broker gateway itself
     * @returns {Promise<BrokerResponse>}
     */
    async getGatewayHealth() {
        try {
            const url = `${this.gatewayUrl}/health`;
            const response = await this.client.get(url);
            
            if (response.status === 200) {
                return new BrokerResponse({
                    success: true,
                    data: response.data,
                    statusCode: response.status
                });
            } else {
                return new BrokerResponse({
                    errors: [{
                        code: 'GATEWAY_UNHEALTHY',
                        message: `Gateway health check failed: ${response.status}`
                    }],
                    statusCode: response.status
                });
            }
        } catch (error) {
            return new BrokerResponse({
                errors: [{
                    code: 'HEALTH_CHECK_ERROR',
                    message: error.message
                }],
                statusCode: 500
            });
        }
    }
}

/**
 * Create a pre-configured broker gateway client
 * @param {string} options.gatewayUrl - URL of broker gateway
 * @param {string} options.hostServerUrl - URL of host server for service discovery
 * @returns {BrokerGatewayClient}
 */
function createClient(options = {}) {
    return new BrokerGatewayClient(options);
}

// Export classes for module usage
module.exports = {
    BrokerGatewayClient,
    ServiceDetails,
    BrokerResponse,
    createClient
};

// Example usage
if (require.main === module) {
    async function main() {
        // Create client
        const client = createClient();
        
        try {
            // Example: Invoke a service operation
            const response = await client.invokeOperation(
                'getUserRegistrationForToken',
                { token: 'sample-token-123' }
            );
            
            if (response.success) {
                console.log('Success:', response.data);
            } else {
                console.error('Error:', response.errors);
            }
            
            // Example: Check service health
            const isHealthy = await client.healthCheck('loginService');
            console.log(`Login service healthy: ${isHealthy}`);
            
            // Example: Register a new service
            const serviceDetails = {
                serviceName: 'nodejs-microservice',
                endpoint: 'http://localhost:3002',
                healthCheck: 'health',
                framework: 'Express'
            };
            
            const registered = await client.registerService(serviceDetails);
            console.log(`Service registration: ${registered ? 'success' : 'failed'}`);
            
        } catch (error) {
            console.error('Example failed:', error.message);
        }
    }
    
    main().catch(console.error);
}
