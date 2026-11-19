import asyncio
from unittest.mock import AsyncMock
from app.registry_client import RegistryClient


async def test_registry_client():
    """Test the registry client functionality"""
    registry_client = RegistryClient(
        registry_url="http://test-registry:8085/api/registry",
        service_name="test-python-broker-gateway"
    )

    # Mock the httpx client
    mock_client = AsyncMock()
    registry_client.client = mock_client

    # Test registration
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True, "message": "Service registered successfully"}
    mock_client.post.return_value = mock_response

    # Test registration
    result = await registry_client.register_service(
        operations=["test"],
        endpoint="http://localhost:8000",
        health_check="http://localhost:8000/health",
        metadata={"type": "test", "language": "python"},
        framework="Python-FastAPI",
        version="1.0.0",
        port=8000
    )

    assert result is True
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "http://test-registry:8085/api/registry/register"

    # Verify the payload
    payload = call_args[1]['json']
    assert payload['serviceName'] == "test-python-broker-gateway"
    assert "test" in payload['operations']
    assert payload['endpoint'] == "http://localhost:8000"

    # Test heartbeat
    mock_response.status_code = 200
    mock_response.json.return_value = {"message": "Heartbeat received"}
    mock_client.post.reset_mock()
    mock_client.post.return_value = mock_response

    heartbeat_result = await registry_client.send_heartbeat()
    assert heartbeat_result is True

    # Check heartbeat call
    mock_client.post.assert_called_with("http://test-registry:8085/api/registry/heartbeat/test-python-broker-gateway")

    print("All registry client tests passed!")


if __name__ == "__main__":
    asyncio.run(test_registry_client())