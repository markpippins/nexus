import asyncio
import logging
from typing import List, Dict, Optional
import httpx
from datetime import datetime

logger = logging.getLogger(__name__)

class RegistryClient:
    def __init__(self, registry_url: str = None, service_name: str = "python-broker-gateway"):
        self.registry_url = registry_url or "http://localhost:8085/api/v1/registry"
        self.service_name = service_name
        self.heartbeat_task = None
        self.client = None
        
    async def initialize(self):
        """Initialize the HTTP client"""
        self.client = httpx.AsyncClient(timeout=10.0)
        
    async def close(self):
        """Close the HTTP client"""
        if self.client:
            await self.client.aclose()
            
    async def register_service(
        self, 
        operations: List[str], 
        endpoint: str, 
        health_check: str = None,
        metadata: Dict = None,
        framework: str = "Python-FastAPI",
        version: str = "1.0.0",
        port: int = 8000
    ) -> bool:
        """Register the service with the service-registry registry"""
        if health_check is None:
            health_check = f"{endpoint}/health"
            
        if metadata is None:
            metadata = {
                "type": "broker-gateway",
                "language": "python",
                "framework": framework
            }
        
        registration_data = {
            "serviceName": self.service_name,
            "operations": operations,
            "endpoint": endpoint,
            "healthCheck": health_check,
            "metadata": metadata,
            "framework": framework,
            "version": version,
            "port": port
        }
        
        try:
            logger.info(f"Registering service '{self.service_name}' with registry: {self.registry_url}")
            response = await self.client.post(
                f"{self.registry_url}/register",
                json=registration_data
            )
            
            if response.status_code in [200, 201]:
                result = response.json()
                logger.info(f"Service registered successfully: {result}")
                return True
            else:
                logger.error(f"Failed to register service. Status: {response.status_code}, Response: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error registering service: {e}")
            return False
    
    async def send_heartbeat(self) -> bool:
        """Send a heartbeat to the registry to maintain service status"""
        try:
            response = await self.client.post(
                f"{self.registry_url}/heartbeat/{self.service_name}"
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.debug(f"Heartbeat sent successfully: {result}")
                return True
            elif response.status_code == 404:
                logger.warning(f"Service {self.service_name} not found in registry, re-registering...")
                return False
            else:
                logger.warning(f"Heartbeat failed. Status: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending heartbeat: {e}")
            return False
    
    async def start_heartbeats(self, interval_seconds: int = 30):
        """Start periodic heartbeat task"""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval_seconds))
        logger.info(f"Started heartbeat loop for {self.service_name} with interval {interval_seconds}s")
    
    async def stop_heartbeats(self):
        """Stop the periodic heartbeat task"""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass
            self.heartbeat_task = None
            logger.info(f"Stopped heartbeat loop for {self.service_name}")
    
    async def _heartbeat_loop(self, interval_seconds: int):
        """Internal heartbeat loop"""
        while True:
            try:
                success = await self.send_heartbeat()
                if not success:
                    # If heartbeat fails (service not found), try to re-register
                    logger.info("Attempting to re-register service after failed heartbeat...")
                    # Note: Re-registration would require storing registration params
                    # For now, just log and continue
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Heartbeat loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}")
                await asyncio.sleep(interval_seconds)
    
    async def deregister_service(self) -> bool:
        """Deregister the service from the registry"""
        try:
            response = await self.client.post(
                f"{self.registry_url}/deregister/{self.service_name}"
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Service deregistered successfully: {result}")
                return True
            else:
                logger.error(f"Failed to deregister service. Status: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error deregistering service: {e}")
