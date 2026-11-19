#!/usr/bin/env python3
"""
Nexus Broker Gateway Python SDK
Lightweight client library for Nexus Broker Gateway services
"""

import json
import logging
import requests
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass


@dataclass
class ServiceDetails:
    """Service details from broker gateway"""
    service_name: str
    endpoint: str
    health_check: Optional[str] = None
    framework: Optional[str] = None
    status: Optional[str] = None
    operations: Optional[str] = None


@dataclass 
class BrokerRequest:
    """Request to broker gateway"""
    service_name: str
    operation: str
    params: Dict[str, Any]
    request_id: Optional[str] = None


@dataclass
class BrokerResponse:
    """Response from broker gateway"""
    success: bool
    data: Optional[Any] = None
    errors: Optional[list] = None
    status_code: int = 200
    raw_response: Optional[str] = None


class BrokerGatewayClient:
    """Lightweight Python client for Nexus Broker Gateway"""
    
    def __init__(self, base_url: str = "http://localhost:8080", 
                 host_server_url: str = "http://localhost:8085"):
        """
        Initialize broker gateway client
        
        Args:
            base_url: URL of the broker gateway
            host_server_url: URL of the host server for service discovery
        """
        self.base_url = base_url.rstrip('/')
        self.host_server_url = host_server_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
        
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
    
    def discover_service(self, operation: str) -> Optional[ServiceDetails]:
        """
        Discover service that can handle the specified operation
        
        Args:
            operation: Operation name (e.g., "getUserRegistrationForToken")
            
        Returns:
            ServiceDetails if found, None otherwise
        """
        try:
            url = f"{self.host_server_url}/api/v1/registry/services/by-operation/{operation}"
            self.logger.info(f"Discovering service for operation: {operation}")
            
            response = self.session.get(url)
            if response.status_code == 200:
                data = response.json()
                if data:
                    service_info = data.get('data', {})
                    return ServiceDetails(
                        service_name=service_info.get('name', ''),
                        endpoint=service_info.get('endpoint', ''),
                        health_check=service_info.get('healthCheck'),
                        framework=service_info.get('framework'),
                        status=service_info.get('status'),
                        operations=service_info.get('operations')
                    )
        except Exception as e:
            self.logger.error(f"Service discovery failed: {e}")
        
        return None
    
    def get_service_details(self, service_name: str) -> Optional[ServiceDetails]:
        """
        Get detailed information about a specific service
        
        Args:
            service_name: Name of the service
            
        Returns:
            ServiceDetails if found, None otherwise
        """
        try:
            url = f"{self.host_server_url}/api/v1/registry/services/{service_name}/details"
            self.logger.info(f"Getting details for service: {service_name}")
            
            response = self.session.get(url)
            if response.status_code == 200:
                data = response.json()
                if data:
                    service_data = data.get('data', {})
                    return ServiceDetails(
                        service_name=service_data.get('serviceName', service_name),
                        endpoint=service_data.get('endpoint', ''),
                        health_check=service_data.get('healthCheck'),
                        framework=service_data.get('framework'),
                        status=service_data.get('status'),
                        operations=service_data.get('operations')
                    )
        except Exception as e:
            self.logger.error(f"Failed to get service details: {e}")
        
        return None
    
    def invoke_operation(self, operation: str, params: Dict[str, Any], 
                     service_name: Optional[str] = None) -> BrokerResponse:
        """
        Invoke an operation on a service through the broker gateway
        
        Args:
            operation: Operation name to invoke
            params: Parameters for the operation
            service_name: Optional service name (discovered if not provided)
            
        Returns:
            BrokerResponse with results
        """
        try:
            # Discover service if not provided
            if not service_name:
                service = self.discover_service(operation)
                if not service:
                    return BrokerResponse(
                        success=False,
                        errors=[{"code": "SERVICE_NOT_FOUND", "message": f"No service found for operation: {operation}"}],
                        status_code=404
                    )
                service_name = service.service_name
            
            # Get service details
            service_details = self.get_service_details(service_name)
            if not service_details:
                return BrokerResponse(
                    success=False,
                    errors=[{"code": "SERVICE_DETAILS_NOT_FOUND", "message": f"Could not get details for service: {service_name}"}],
                    status_code=500
                )
            
            # Build operation URL
            endpoint = service_details.endpoint
            operation_url = f"{endpoint.rstrip('/')}/{operation}" if endpoint else f"/{operation}"
            
            self.logger.info(f"Invoking operation {operation} on service {service_name}")
            
            # Invoke operation
            response = self.session.post(operation_url, json=params)
            
            if response.status_code in [200, 201]:
                self.logger.info(f"Successfully invoked {operation} on {service_name}")
                return BrokerResponse(
                    success=True,
                    data=response.json() if response.content else None,
                    status_code=response.status_code,
                    raw_response=response.text
                )
            else:
                self.logger.warning(f"Operation failed with status {response.status_code}")
                return BrokerResponse(
                    success=False,
                    errors=[{"code": "OPERATION_FAILED", "message": f"HTTP {response.status_code}: {response.text}"}],
                    status_code=response.status_code,
                    raw_response=response.text
                )
                
        except Exception as e:
            self.logger.error(f"Failed to invoke operation {operation}: {e}")
            return BrokerResponse(
                success=False,
                errors=[{"code": "CLIENT_ERROR", "message": str(e)}],
                status_code=500
            )
    
    def health_check(self, service_name: str) -> bool:
        """
        Perform health check on a specific service
        
        Args:
            service_name: Name of the service to check
            
        Returns:
            True if healthy, False otherwise
        """
        try:
            service_details = self.get_service_details(service_name)
            if not service_details:
                return False
            
            health_url = service_details.health_check
            if not health_url:
                # Default to /health endpoint
                health_url = f"{service_details.endpoint}/health"
            elif not health_url.startswith('/'):
                health_url = f"{service_details.endpoint}/{health_url}"
            
            self.logger.debug(f"Health checking {service_name} at {health_url}")
            
            response = self.session.get(health_url)
            is_healthy = response.status_code == 200
            self.logger.debug(f"Health check for {service_name}: {'healthy' if is_healthy else 'unhealthy'}")
            
            return is_healthy
            
        except Exception as e:
            self.logger.warning(f"Health check failed for {service_name}: {e}")
            return False
    
    def register_service(self, service_details: ServiceDetails) -> bool:
        """
        Register a new service with the broker gateway
        
        Args:
            service_details: Details of the service to register
            
        Returns:
            True if registration successful, False otherwise
        """
        try:
            url = f"{self.host_server_url}/api/v1/registry/register"
            self.logger.info(f"Registering service: {service_details.service_name}")
            
            service_data = {
                "serviceName": service_details.service_name,
                "endpoint": service_details.endpoint,
                "healthCheck": service_details.health_check,
                "framework": service_details.framework,
                "operations": service_details.operations
            }
            
            response = self.session.post(url, json=service_data)
            success = response.status_code in [200, 201]
            
            if success:
                self.logger.info(f"Successfully registered service: {service_details.service_name}")
            else:
                self.logger.error(f"Failed to register service: {response.status_code} {response.text}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Service registration failed: {e}")
            return False
    
    def get_gateway_health(self) -> BrokerResponse:
        """
        Check health of the broker gateway itself
        
        Returns:
            BrokerResponse with gateway health status
        """
        try:
            url = f"{self.base_url}/health"
            response = self.session.get(url)
            
            if response.status_code == 200:
                return BrokerResponse(
                    success=True,
                    data=response.json(),
                    status_code=response.status_code
                )
            else:
                return BrokerResponse(
                    success=False,
                    errors=[{"code": "GATEWAY_UNHEALTHY", "message": f"Gateway health check failed: {response.status_code}"}],
                    status_code=response.status_code
                )
        except Exception as e:
            return BrokerResponse(
                success=False,
                errors=[{"code": "HEALTH_CHECK_ERROR", "message": str(e)}],
                status_code=500
            )


# Convenience functions for quick usage
def create_client(gateway_url: str = "http://localhost:8080", 
                 host_server_url: str = "http://localhost:8085") -> BrokerGatewayClient:
    """Create a pre-configured broker gateway client"""
    return BrokerGatewayClient(gateway_url, host_server_url)


# Example usage
if __name__ == "__main__":
    # Create client
    client = create_client()
    
    # Example: Invoke a service operation
    response = client.invoke_operation(
        "getUserRegistrationForToken",
        {"token": "sample-token-123"}
    )
    
    if response.success:
        print(f"Success: {response.data}")
    else:
        print(f"Error: {response.errors}")
    
    # Example: Check service health
    is_healthy = client.health_check("loginService")
    print(f"Login service healthy: {is_healthy}")
    
    # Example: Register a new service
    service = ServiceDetails(
        service_name="python-microservice",
        endpoint="http://localhost:3001",
        health_check="health",
        framework="FastAPI"
    )
    
    if client.register_service(service):
        print("Service registered successfully!")
    else:
        print("Service registration failed")