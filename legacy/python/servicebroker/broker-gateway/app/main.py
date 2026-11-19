import logging
import os
from fastapi import FastAPI, HTTPException
from app.models import ServiceRequest, ServiceResponse
from app.broker import Broker
from app.registry_client import RegistryClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Broker Service Python")
broker = Broker()

# Initialize registry client
registry_client = RegistryClient(
    registry_url=os.getenv("SERVICE_REGISTRY_URL", "http://localhost:8085/api/v1/registry"),
    service_name="python-broker-gateway"
)

# --- Sample Handler ---
async def test_handler(params: dict):
    logger.info(f"Test handler called with params: {params}")
    return {"message": "Hello from Python Broker!", "received_params": params}

# Register the sample handler
broker.register("testBroker", "test", test_handler)

# --- Endpoints ---

@app.post("/api/broker/submitRequest", response_model=ServiceResponse)
async def submit_request(request: ServiceRequest):
    response = await broker.submit(request)
    if not response.meta.ok:
        # In the Java controller, it returns 400 Bad Request for errors.
        # We can do the same, or just return the response with 200 OK but ok=False.
        # The Java code: return ResponseEntity.badRequest().body(response);
        # So we should probably set the status code to 400 if not ok.
        from fastapi.responses import JSONResponse
        from fastapi.encoders import jsonable_encoder
        
        return JSONResponse(
            status_code=400,
            content=jsonable_encoder(response)
        )
    return response

@app.post("/api/broker/testBroker", response_model=ServiceResponse)
async def test_broker():
    # Mimic the Java testBroker endpoint
    request = ServiceRequest(
        service="testBroker",
        operation="test",
        params={},
        requestId="test-request"
    )
    return await submit_request(request)

# --- Lifecycle Events ---

@app.on_event("startup")
async def startup_event():
    logger.info("Starting up python-broker-gateway...")

    # Initialize registry client
    await registry_client.initialize()

    # Register the service with service-registry
    service_host = os.getenv("SERVICE_HOST", "localhost")
    service_port = os.getenv("PORT", "8000")
    endpoint = f"http://{service_host}:{service_port}"

    operations = ["test"]  # Add any operations this service supports

    success = await registry_client.register_service(
        operations=operations,
        endpoint=endpoint,
        health_check=f"{endpoint}/health",
        metadata={
            "type": "broker-gateway",
            "language": "python",
            "framework": "FastAPI"
        },
        framework="Python-FastAPI",
        version="1.0.0",
        port=int(service_port)
    )

    if success:
        # Start heartbeat loop
        heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30"))
        await registry_client.start_heartbeats(heartbeat_interval)
        logger.info("Service registered and heartbeat started successfully")
    else:
        logger.error("Failed to register service with registry")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down python-broker-gateway...")

    # Stop heartbeats
    await registry_client.stop_heartbeats()

    # Deregister the service
    await registry_client.deregister_service()

    # Close registry client
    await registry_client.close()


@app.get("/health")
def health_check():
    return {"status": "UP"}
